"""Encrypted connector-credential storage (keeperdb_integration_tasks #3,
per the design in requirements-specs/connector_tasks/02).

Separate table from `connections` on purpose (defense in depth): even a SQL
injection that dumps `connections` won't reveal secrets — the ciphertext
lives here, and the key lives only in the SECRETS_ENCRYPTION_KEY env var.
The unique constraint on connection_id is also what makes the backfill
migration race-safe (one vault row per connection, ever).
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class ConnectionSecret(Base):
    __tablename__ = "connection_secrets"
    __table_args__ = (
        UniqueConstraint("connection_id", name="uq_connection_secret"),
    )

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(Integer, ForeignKey("connections.id", ondelete="CASCADE"),
                           nullable=False, unique=True, index=True)
    ciphertext = Column(Text, nullable=False)   # base64(nonce + AES-256-GCM ciphertext)
    key_id = Column(String, nullable=False)     # which encryption key encrypted this row
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    rotated_at = Column(DateTime(timezone=True), nullable=True)
