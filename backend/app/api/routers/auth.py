import logging
import secrets
import msal
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.rbac_service import backfill_user_roles

logger = logging.getLogger(__name__)
router = APIRouter()
bearer = HTTPBearer(auto_error=False)

ENTRA_STATE_COOKIE = "entra_state"


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    email: str


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    logger.info(f"🔍 [get_current_user] incoming request headers: {dict(request.headers)}")
    
    # Databricks proxy might strip the Authorization header. Check X-DataOne-Auth fallback.
    token = None
    if credentials:
        token = credentials.credentials
    else:
        fallback = request.headers.get("X-DataOne-Auth")
        if fallback and fallback.lower().startswith("bearer "):
            token = fallback[7:]
            
    if not token:
        logger.warning("❌ [get_current_user] No bearer credentials provided")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    try:
        payload = AuthService.decode_token(token)
    except Exception as e:
        logger.warning(f"❌ [get_current_user] decode_token failed: {e}")
        raise
    email: str = payload.get("sub", "")
    user = db.query(User).filter(User.email == email, User.is_active == True).first()
    if user is None:
        logger.warning(f"❌ [get_current_user] User not found for email: {email}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    logger.info(f"🔍 Login attempt - Email: {req.email}, Password length: {len(req.password)}")
    
    # Check if any users exist at all
    user_count = db.query(User).count()
    logger.info(f"🔍 Total users in database: {user_count}")
    
    # Check if this specific user exists
    user = db.query(User).filter(User.email == req.email).first()
    if user is None:
        logger.warning(f"❌ Login failed: No user found with email {req.email}")
        # List all users for debugging
        all_users = db.query(User.email, User.is_active).all()
        logger.info(f"🔍 All users in database: {[(email, active) for email, active in all_users]}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    
    logger.info(f"🔍 User found: {user.email}, Active: {user.is_active}, Has password: {user.hashed_password is not None}")
    
    if not user.is_active:
        logger.warning(f"❌ Login failed: User {req.email} is not active")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    
    if not AuthService.verify_password(req.password, user.hashed_password):
        logger.warning(f"❌ Login failed: Invalid password for email {req.email}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    
    token = AuthService.create_access_token({"sub": user.email, "role": user.role})
    logger.info("✅ User '%s' logged in successfully", user.email)
    return LoginResponse(access_token=token, role=user.role, email=user.email)


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "email": current_user.email, "role": current_user.role, "llm_model": current_user.llm_model}

from pydantic import BaseModel
class LLMModelUpdateRequest(BaseModel):
    llm_model: str

@router.put("/me/llm_model")
def update_llm_model(req: LLMModelUpdateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    current_user.llm_model = req.llm_model
    db.commit()
    return {"status": "ok", "llm_model": current_user.llm_model}


def _entra_client() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        settings.ENTRA_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}",
        client_credential=settings.ENTRA_CLIENT_SECRET,
    )


def _require_entra_configured() -> None:
    if not settings.ENTRA_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft Entra login is not configured",
        )


@router.get("/entra/login")
def entra_login():
    _require_entra_configured()

    state = secrets.token_urlsafe(32)
    auth_url = _entra_client().get_authorization_request_url(
        scopes=["User.Read"],
        state=state,
        redirect_uri=settings.ENTRA_REDIRECT_URI,
    )
    response = RedirectResponse(auth_url)
    response.set_cookie(
        ENTRA_STATE_COOKIE,
        state,
        max_age=300,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


def _maybe_auto_provision(db: Session, email: str) -> User | None:
    """Temporary stopgap (see ENTRA_AUTO_PROVISION_DOMAIN) — auto-create an
    admin account for any Entra login on the configured domain. Off by
    default; meant to be unset once real per-user provisioning lands."""
    domain = settings.ENTRA_AUTO_PROVISION_DOMAIN
    if not domain or not email.endswith(f"@{domain.lower()}"):
        return None

    user = User(
        email=email,
        hashed_password=AuthService.hash_password(secrets.token_urlsafe(32)),
        role="admin",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    backfill_user_roles(db)
    logger.warning(
        "Entra auto-provisioned admin account for '%s' (ENTRA_AUTO_PROVISION_DOMAIN=%s)",
        email, domain,
    )
    return user


@router.get("/entra/callback")
def entra_callback(request: Request, db: Session = Depends(get_db)):
    _require_entra_configured()

    def redirect_with_error(error: str) -> RedirectResponse:
        response = RedirectResponse(f"{settings.FRONTEND_LOGIN_URL}?entra_error={error}")
        response.delete_cookie(ENTRA_STATE_COOKIE)
        return response

    code = request.query_params.get("code")
    returned_state = request.query_params.get("state")
    cookie_state = request.cookies.get(ENTRA_STATE_COOKIE)

    if not code or not returned_state or not cookie_state or returned_state != cookie_state:
        logger.warning("Entra callback rejected: missing/mismatched state")
        return redirect_with_error("state_mismatch")

    try:
        result = _entra_client().acquire_token_by_authorization_code(
            code,
            scopes=["User.Read"],
            redirect_uri=settings.ENTRA_REDIRECT_URI,
        )
    except Exception:
        logger.exception("Entra token exchange failed")
        return redirect_with_error("token_exchange_failed")

    claims = result.get("id_token_claims")
    if not claims:
        logger.warning("Entra token exchange returned no id_token_claims: %s", result.get("error"))
        return redirect_with_error("token_exchange_failed")

    if claims.get("tid") != settings.ENTRA_TENANT_ID:
        logger.warning("Entra callback tenant mismatch: got '%s'", claims.get("tid"))
        return redirect_with_error("tenant_mismatch")

    email = claims.get("preferred_username") or claims.get("email")
    if not email:
        logger.warning("Entra callback missing email/preferred_username claim")
        return redirect_with_error("no_account")

    email = email.strip().lower()
    user = (
        db.query(User)
        .filter(func.lower(User.email) == email, User.is_active == True)
        .first()
    )
    if user is None:
        user = _maybe_auto_provision(db, email)
    if user is None:
        logger.info("Entra login for '%s' rejected: no matching DataOne account", email)
        return redirect_with_error("no_account")

    token = AuthService.create_access_token({"sub": user.email, "role": user.role})
    logger.info("User '%s' logged in via Microsoft Entra", user.email)

    response = RedirectResponse(f"{settings.FRONTEND_LOGIN_URL}?entra_token={token}")
    response.delete_cookie(ENTRA_STATE_COOKIE)
    return response
