"""Authenticated global catalog search for the enterprise shell."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.schema_catalog import GlobalSearchResponse
from app.services.schema_catalog_service import SchemaCatalogService

router = APIRouter()


@router.get("", response_model=GlobalSearchResponse)
def global_search(
    q: str = Query(..., min_length=2, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return SchemaCatalogService.search_all(db, q, page=page, page_size=page_size)
