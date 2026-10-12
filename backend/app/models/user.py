from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.sql import func
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=True)  # Nullable for OAuth users
    full_name = Column(String, nullable=True)
    role = Column(String, nullable=False, default="viewer")  # admin | editor | viewer
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    llm_model = Column(String, nullable=True)  # Stores selected Databricks LLM model
    
    # Databricks OAuth fields
    databricks_user_id = Column(String, nullable=True, index=True)
    databricks_access_token = Column(Text, nullable=True)  # Encrypted in production
    databricks_refresh_token = Column(Text, nullable=True)  # Encrypted in production
    databricks_token_expires_at = Column(DateTime(timezone=True), nullable=True)
