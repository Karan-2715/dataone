"""
Databricks OAuth2 Authentication Service

Handles OAuth2 authentication (authorization code + PKCE) for DataOne:
- Sign-in with the user's own Databricks identity (no service principal)
- Token management (access + refresh) used by the workspace installer
- Unity Catalog permission mapping to DataOne roles
- User provisioning from Databricks workspace users
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple

from databricks.sdk import WorkspaceClient
from databricks.sdk.oauth import Consent, OAuthClient, SessionCredentials, Token
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.oauth_login_state import OAuthLoginState
from app.models.user import User

logger = logging.getLogger(__name__)

LOGIN_STATE_TTL = timedelta(minutes=10)


def safe_redirect_path(path: Optional[str]) -> str:
    """Only same-site absolute paths — never an external URL (open-redirect guard)."""
    if path and path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return path
    return "/dashboard"


class DatabricksAuthService:
    """
    Service for handling Databricks OAuth2 authentication and user management.

    The SDK OAuth client needs the workspace's OIDC metadata (a network call), so it
    is built lazily on first use rather than at import time.
    """

    def __init__(self):
        self.workspace_url = self._normalize(settings.DATABRICKS_WORKSPACE_URL or settings.DATABRICKS_HOST)
        self.client_id = settings.DATABRICKS_OAUTH_CLIENT_ID
        self.client_secret = settings.DATABRICKS_OAUTH_CLIENT_SECRET
        self.redirect_uri = f"{settings.BACKEND_URL}/api/v1/auth/databricks/callback"
        self.scopes = settings.DATABRICKS_OAUTH_SCOPES.split()
        self._oauth_client: Optional[OAuthClient] = None

    @staticmethod
    def _normalize(host: Optional[str]) -> Optional[str]:
        if not host:
            return None
        host = host.strip().rstrip("/")
        return host if host.startswith(("https://", "http://")) else f"https://{host}"

    @property
    def is_configured(self) -> bool:
        """An OAuth app (client id) and a workspace to sign in against are both set."""
        return bool(self.client_id and self.workspace_url)

    @property
    def oauth_client(self) -> Optional[OAuthClient]:
        if not self.is_configured:
            return None
        if self._oauth_client is None:
            try:
                self._oauth_client = OAuthClient.from_host(
                    host=self.workspace_url,
                    client_id=self.client_id,
                    redirect_url=self.redirect_uri,
                    scopes=self.scopes,
                    client_secret=self.client_secret,
                )
                logger.info("Databricks OAuth client initialized for %s", self.workspace_url)
            except Exception as e:
                logger.error("Failed to initialize Databricks OAuth client: %s", e)
                return None
        return self._oauth_client

    def begin_login(self, db: Session, redirect_to: str = "/dashboard") -> str:
        """Start sign-in: persist the PKCE consent and return the Databricks authorization URL."""
        client = self.oauth_client
        if client is None:
            raise ValueError("Databricks OAuth is not configured")
        consent = client.initiate_consent()
        raw = consent.as_dict()

        cutoff = datetime.now(timezone.utc) - LOGIN_STATE_TTL
        db.query(OAuthLoginState).filter(OAuthLoginState.created_at < cutoff).delete()
        db.add(OAuthLoginState(state=raw["state"], consent=raw, redirect_to=safe_redirect_path(redirect_to)))
        db.commit()
        logger.info("[databricks-oauth] stage=begin_login")
        return consent.authorization_url

    def complete_login(self, db: Session, query: Dict[str, str]) -> Tuple[Dict[str, Any], str]:
        """Finish sign-in from the callback query (code + state). Returns (token data, redirect path)."""
        state = query.get("state")
        row = db.get(OAuthLoginState, state) if state else None
        if row is None:
            raise ValueError("Unknown or expired sign-in attempt; start again")
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        redirect_to = row.redirect_to
        raw = row.consent
        db.delete(row)  # single use, even if the exchange below fails
        db.commit()
        if datetime.now(timezone.utc) - created > LOGIN_STATE_TTL:
            raise ValueError("Sign-in attempt expired; start again")

        consent = Consent.from_dict(raw, client_secret=self.client_secret)
        creds = consent.exchange_callback_parameters(query)
        tok = creds.token()
        logger.info("[databricks-oauth] stage=complete_login")
        return {
            "access_token": tok.access_token,
            "refresh_token": tok.refresh_token,
            "expires_in": self._seconds_until(tok.expiry),
        }, redirect_to

    @staticmethod
    def _seconds_until(expiry: Optional[datetime]) -> int:
        if expiry is None:
            return 3600
        now = datetime.now(expiry.tzinfo) if expiry.tzinfo else datetime.now()
        return max(0, int((expiry - now).total_seconds()))

    def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """
        Fetch user information from Databricks using access token.
        
        Args:
            access_token: Valid OAuth access token
            
        Returns:
            User information (email, name, id, etc.)
        """
        try:
            # Create workspace client with OAuth token
            ws = WorkspaceClient(
                host=self.workspace_url,
                token=access_token,
                auth_type="pat",
            )
            
            # Get current user
            current_user = ws.current_user.me()
            
            user_info = {
                "id": current_user.id,
                "email": current_user.emails[0].value if current_user.emails else None,
                "username": current_user.user_name,
                "display_name": current_user.display_name,
                "active": current_user.active
            }
            
            logger.info(f"Fetched user info for: {user_info['email']}")
            return user_info
            
        except Exception as e:
            logger.error(f"Failed to fetch user info: {e}")
            raise

    def get_user_permissions(self, access_token: str) -> List[str]:
        """
        Get Unity Catalog permissions for the authenticated user.
        
        Args:
            access_token: Valid OAuth access token
            
        Returns:
            List of permission strings
        """
        try:
            ws = WorkspaceClient(
                host=self.workspace_url,
                token=access_token,
                auth_type="pat",
            )
            
            # Get user's Unity Catalog grants
            # This is a simplified version - actual implementation would check:
            # - Catalog-level grants
            # - Schema-level grants
            # - Table-level grants
            # - Function grants
            
            permissions = []
            
            # Check if user has SQL warehouse access
            try:
                warehouses = ws.warehouses.list()
                if warehouses:
                    permissions.append("sql:execute")
            except Exception:
                pass
            
            # Check Unity Catalog access
            try:
                catalogs = ws.catalogs.list()
                if catalogs:
                    permissions.append("unity_catalog:read")
            except Exception:
                pass
            
            logger.info(f"User permissions: {permissions}")
            return permissions
            
        except Exception as e:
            logger.error(f"Failed to get user permissions: {e}")
            return []

    def map_permissions_to_role(self, permissions: List[str]) -> str:
        """
        Map Databricks Unity Catalog permissions to DataOne role.
        
        Args:
            permissions: List of permission strings from Unity Catalog
            
        Returns:
            DataOne role (admin, editor, viewer)
        """
        # Role mapping logic based on Unity Catalog grants
        # In production, this would be more sophisticated
        
        if "unity_catalog:admin" in permissions:
            return "admin"
        elif "sql:execute" in permissions and "unity_catalog:read" in permissions:
            return "editor"
        elif "unity_catalog:read" in permissions:
            return "viewer"
        else:
            # Default to viewer if no specific permissions found
            return "viewer"

    async def provision_or_update_user(
        self, 
        db: Session,
        user_info: Dict[str, Any],
        access_token: str,
        refresh_token: str,
        expires_in: int
    ) -> User:
        """
        Create or update DataOne user from Databricks OAuth.
        
        Args:
            db: Database session
            user_info: User information from Databricks
            access_token: OAuth access token
            refresh_token: OAuth refresh token
            expires_in: Token expiration time in seconds
            
        Returns:
            DataOne User object
        """
        email = user_info.get("email")
        if not email:
            raise ValueError("User email not found in Databricks user info")
        
        # Check if user already exists
        user = db.query(User).filter(User.email == email).first()
        
        # Get user permissions and map to role
        permissions = self.get_user_permissions(access_token)
        role = self.map_permissions_to_role(permissions)
        
        if user:
            # Update existing user
            user.full_name = user_info.get("display_name", user.full_name)
            user.role = role
            user.is_active = user_info.get("active", True)
            
            # Update OAuth tokens (stored securely, encrypted in production)
            user.databricks_access_token = access_token
            user.databricks_refresh_token = refresh_token
            user.databricks_token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            
            logger.info(f"Updated existing user: {email} with role {role}")
        else:
            # Create new user
            user = User(
                email=email,
                full_name=user_info.get("display_name", email),
                role=role,
                is_active=user_info.get("active", True),
                databricks_user_id=user_info.get("id"),
                databricks_access_token=access_token,
                databricks_refresh_token=refresh_token,
                databricks_token_expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
                # No password for OAuth users
                hashed_password=None
            )
            db.add(user)
            logger.info(f"Created new user: {email} with role {role}")
        
        db.commit()
        db.refresh(user)
        
        return user

    def ensure_token_valid(self, user: User, db: Session) -> str:
        """
        Return a usable access token for ``user``, refreshing it with the stored
        refresh token when it is expired or about to expire.
        """
        if not user.databricks_access_token:
            raise ValueError("User has no Databricks access token")

        expires = user.databricks_token_expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if expires is None or expires - timedelta(minutes=5) > now:
            return user.databricks_access_token

        if not (user.databricks_refresh_token and self.is_configured):
            raise ValueError("Databricks token expired and cannot be refreshed; sign in again")

        logger.info("Token expired for user %s, refreshing...", user.email)
        stale = Token(access_token=user.databricks_access_token, token_type="Bearer",
                      refresh_token=user.databricks_refresh_token, expiry=datetime.now() - timedelta(seconds=1))
        creds = SessionCredentials(
            token=stale,
            token_endpoint=f"{self.workspace_url}/oidc/v1/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_url=self.redirect_uri,
        )
        fresh = creds.token()  # performs the refresh-token grant because the token is expired
        user.databricks_access_token = fresh.access_token
        user.databricks_refresh_token = fresh.refresh_token or user.databricks_refresh_token
        user.databricks_token_expires_at = now + timedelta(seconds=self._seconds_until(fresh.expiry))
        db.commit()
        db.refresh(user)
        return user.databricks_access_token


# Global instance
databricks_auth_service = DatabricksAuthService()
