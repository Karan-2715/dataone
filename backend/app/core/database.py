from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# For sqlite connection url if we use it, otherwise Postgres
# Prioritize APP_DATABASE_URL to bypass Databricks Native Apps overwriting DATABASE_URL
db_url = settings.APP_DATABASE_URL if settings.APP_DATABASE_URL else settings.DATABASE_URL
engine = create_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
