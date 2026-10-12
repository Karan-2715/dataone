"""FastAPI dependencies for role-based access control."""
from __future__ import annotations

from typing import Iterable

from fastapi import Depends, HTTPException, status

from app.api.routers.auth import get_current_user
from app.models.user import User


def require_role(*allowed: str):
    """Dependency factory: enforce that the current user's role is in ``allowed``."""
    allowed_set = set(allowed)

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"role '{user.role}' not authorized; need one of "
                    f"{sorted(allowed_set)}"
                ),
            )
        return user

    return _dep
