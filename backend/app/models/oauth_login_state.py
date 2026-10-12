"""Pending Databricks OAuth (authorization-code + PKCE) logins.

The SDK's ``Consent`` holds the PKCE verifier between redirect-out and
callback. It is stored here (not in process memory) so the callback works
whichever gunicorn worker receives it. Rows are single-use and expire quickly.
"""
from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, String
from sqlalchemy.sql import func

from app.core.database import Base


class OAuthLoginState(Base):
    __tablename__ = "oauth_login_states"

    state = Column(String(128), primary_key=True)
    consent = Column(JSON, nullable=False)
    redirect_to = Column(String(512), nullable=False, default="/dashboard")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
