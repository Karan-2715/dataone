"""Dashboard aggregation endpoint (dashboard_tasks #1).

One authenticated read-only endpoint; all fan-out, caching, and
role-scoping live in DashboardService. Role filtering is in-band
(restricted tiles come back as ``unavailable``) so the endpoint itself
never 403s — every authenticated role gets a 200.
"""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.dashboard import DashboardSummary
from app.services.dashboard_service import DashboardService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary(
    range: str = Query("7d", pattern="^(24h|7d|30d)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Unified KPI tiles + activity feed for the dashboard home page."""
    return DashboardService(db).get_summary(range=range, user=user)
