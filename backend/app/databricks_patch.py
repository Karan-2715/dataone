"""
Patch for running DataOne in Databricks Apps environment.
Disables features that require external services (Redis, PostgreSQL-specific features).
"""
import os

def is_databricks_mode():
    """Check if running in Databricks Apps mode."""
    return os.getenv("DATABRICKS_NATIVE_MODE", "false").lower() == "true"

def get_celery_broker():
    """Get appropriate Celery broker for environment."""
    if is_databricks_mode():
        # Use in-memory broker in Databricks mode
        return "memory://"
    return os.getenv("CELERY_BROKER_URL", "redis://broker:6379/0")
