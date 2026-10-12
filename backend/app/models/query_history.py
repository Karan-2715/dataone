from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index
from sqlalchemy.sql import func
from app.core.database import Base


class QueryHistory(Base):
    __tablename__ = "query_history"

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(Integer, ForeignKey("connections.id", ondelete="SET NULL"), nullable=True)
    connection_name = Column(String, nullable=True)
    natural_query = Column(Text, nullable=False)
    generated_sql = Column(Text, nullable=True)
    method = Column(String, nullable=True)
    confidence = Column(Integer, nullable=True)
    row_count = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    report_type = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_query_history_conn_created", "connection_id", "created_at"),
    )
