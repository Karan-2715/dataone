import os
import logging

from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings
from app.core.database_runtime import resolve_database_runtime

logger = logging.getLogger(__name__)

database_runtime = resolve_database_runtime(
    configured_database_url=settings.DATABASE_URL,
    app_database_url=settings.APP_DATABASE_URL,
)


def _create_lakebase_engine():
    """Create a Lakebase engine whose pooled connections get fresh OAuth tokens."""

    from databricks.sdk import WorkspaceClient

    endpoint = os.environ["LAKEBASE_ENDPOINT"]
    workspace = WorkspaceClient()
    url = URL.create(
        "postgresql+psycopg2",
        username=os.environ["PGUSER"],
        host=os.environ["PGHOST"],
        port=int(os.environ["PGPORT"]),
        database=os.environ["PGDATABASE"],
        query={"sslmode": os.environ.get("PGSSLMODE", "require")},
    )
    lakebase_engine = create_engine(url, pool_pre_ping=True, pool_recycle=3000)

    @event.listens_for(lakebase_engine, "do_connect")
    def _provide_oauth_token(_dialect, _connection_record, _cargs, connection_parameters):
        credential = workspace.postgres.generate_database_credential(endpoint=endpoint)
        connection_parameters["password"] = credential.token

    return lakebase_engine


if database_runtime.mode == "lakebase":
    engine = _create_lakebase_engine()
elif database_runtime.url.startswith("sqlite:"):
    engine = create_engine(
        database_runtime.url,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_engine(database_runtime.url, pool_pre_ping=True)

if not database_runtime.persistent:
    logger.warning(
        "[startup] DataOne is using temporary setup storage at %s; attach a "
        "Lakebase resource before production use.",
        database_runtime.url,
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
