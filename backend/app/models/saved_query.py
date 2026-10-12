from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index
from sqlalchemy.sql import func
from app.core.database import Base


class SavedQuery(Base):
    """A user-named SQL query saved for reload in Query Studio (QS-T6)."""
    __tablename__ = "saved_queries"

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(Integer, ForeignKey("connections.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    sql_text = Column(Text, nullable=False)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_saved_queries_connection_created", "connection_id", "created_at"),
    )
