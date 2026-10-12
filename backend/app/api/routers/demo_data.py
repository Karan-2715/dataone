"""Demo/sample data router — opt-in, every logged-in user can load or
remove it from the dashboard. Not seeded automatically at startup (see
`app.main.lifespan`'s docstring). Per-user (tenant_isolation_tasks slice
#1): each caller only ever sees/loads/removes their own copy."""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services.demo_data_service import (
    is_demo_data_loaded,
    load_demo_data,
    unload_demo_data,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/status")
def demo_data_status(db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    return {"loaded": is_demo_data_loaded(db, owner_email=user.email)}


@router.post("/load")
def demo_data_load(db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    result = load_demo_data(db, actor=user.email, owner_email=user.email)
    logger.info("Demo data loaded by '%s'", user.email)
    return result


@router.delete("")
def demo_data_unload(db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    result = unload_demo_data(db, actor=user.email, owner_email=user.email)
    logger.info("Demo data removed by '%s'", user.email)
    return result
