"""DB-level append-only enforcement for audit_log (AUDIT-T3, FR2).

The API surface already never exposes an UPDATE/DELETE for audit events
(the router has no PUT/DELETE route on /audit/events), but that's only an
application-layer guarantee. This installs a DB trigger that rejects any
UPDATE or DELETE on the audit_log table outright, so the append-only
property holds even against direct DB access — a stray migration, an admin
console, a bug in an unrelated module.

Idempotent: safe to call on every app startup (CREATE OR REPLACE / IF NOT
EXISTS throughout).
"""
import logging

from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_POSTGRES_SQL = """
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;
CREATE TRIGGER audit_log_no_update
BEFORE UPDATE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();

DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log;
CREATE TRIGGER audit_log_no_delete
BEFORE DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
"""

_SQLITE_SQL = """
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;
"""


def install_audit_append_only_guard(engine: Engine) -> None:
    """Create the append-only trigger(s) for the current DB dialect.

    Call once after Base.metadata.create_all() so the audit_log table
    already exists. No-op (with a warning) on dialects other than
    postgresql/sqlite.
    """
    dialect = engine.dialect.name
    if dialect == "postgresql":
        sql = _POSTGRES_SQL
    elif dialect == "sqlite":
        sql = _SQLITE_SQL
    else:
        logger.warning(
            "No append-only trigger implementation for DB dialect '%s' — "
            "audit_log is only application-level append-only on this DB.",
            dialect,
        )
        return

    # Both trigger bodies contain their own internal semicolons (the
    # PL/pgSQL function body, the SQLite BEGIN...END block), so this can't
    # be split into separate single-statement executes — that would sever
    # the bodies mid-statement. Run it as one script against the raw DBAPI
    # connection instead:
    #   - sqlite3 requires executescript() for multi-statement SQL (its
    #     cursor.execute() rejects more than one statement outright).
    #   - psycopg2 sends the whole string to Postgres's simple query
    #     protocol in one call, which executes multiple ;-separated
    #     statements (including dollar-quoted bodies) correctly.
    raw = engine.raw_connection()
    try:
        if dialect == "sqlite":
            raw.executescript(sql)
        else:
            cursor = raw.cursor()
            try:
                cursor.execute(sql)
            finally:
                cursor.close()
        raw.commit()
    finally:
        raw.close()

    logger.info("[audit] append-only trigger installed (dialect=%s)", dialect)
