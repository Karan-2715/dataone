from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey
from app.core.database import Base

class SchemaSnapshot(Base):
    __tablename__ = "schema_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(Integer, ForeignKey("connections.id", ondelete="CASCADE"), unique=True, index=True)
    connection_name = Column(String, nullable=True)
    schema_hash = Column(String, nullable=True)
    schema_json = Column(JSON, nullable=False)
    captured_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
