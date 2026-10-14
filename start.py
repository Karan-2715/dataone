#!/usr/bin/env python3
"""
Entry point for DataOne in Databricks Apps.

Unified deployment: builds the Next.js frontend (if not already built),
then starts the FastAPI backend which serves both the API and the static
frontend from the same origin. This avoids all cross-origin issues with
the Databricks Apps reverse-proxy.
"""
import os
import sys
import subprocess

repo_root = os.path.dirname(os.path.abspath(__file__))
backend_path = os.path.join(repo_root, "backend")
sys.path.insert(0, backend_path)

from app.core.runtime_config import configure_databricks_runtime_environment

# ── Ports & URLs ──────────────────────────────────────────────────────────────
databricks_port = int(
    os.getenv("DATABRICKS_APP_PORT") or
    os.getenv("PORT") or
    os.getenv("APP_PORT") or
    "8080"
)

is_databricks = bool(os.getenv("DATABRICKS_APP_PORT"))
configure_databricks_runtime_environment()

# Single app URL — frontend and backend are served from the same Databricks App
BACKEND_APP_URL = os.getenv("DATABRICKS_APP_URL") or "https://dataonetest-7474652115156015.aws.databricksapps.com"

if is_databricks:
    # Ensure it doesn't end with a slash
    base_url = BACKEND_APP_URL.rstrip("/")
    backend_url   = base_url
    frontend_url  = base_url       # same app serves the frontend
    frontend_login_url = base_url + "/signin"
else:
    backend_url        = f"http://localhost:{databricks_port}"
    frontend_url       = f"http://localhost:{databricks_port}"
    frontend_login_url = f"http://localhost:{databricks_port}/signin"

# ── Environment variables ─────────────────────────────────────────────────────
os.environ["LOG_LEVEL"]      = "INFO"
os.environ["BACKEND_URL"]          = backend_url
os.environ["FRONTEND_URL"]         = frontend_url
os.environ["FRONTEND_LOGIN_URL"]   = frontend_login_url
os.environ["DATABRICKS_NATIVE_MODE"] = "true" if is_databricks else os.getenv(
    "DATABRICKS_NATIVE_MODE", "false"
)
os.environ["ENABLE_UNITY_CATALOG"]   = "true"
os.environ["ENABLE_EXTERNAL_CONNECTORS"] = "true"
os.environ["DATABRICKS_USE_LLM"]  = "false"
os.environ["OLLAMA_HOST"]         = "http://localhost:11434"
# CORS: single app — wildcard is fine (same-origin requests don't hit CORS)
os.environ.pop("CORS_ALLOWED_ORIGINS", None)

if is_databricks:
    os.environ["CELERY_BROKER_URL"]    = "redis://disabled"
    os.environ["CELERY_RESULT_BACKEND"] = "disabled"
    os.environ["DISABLE_CELERY"]       = "true"
else:
    os.environ["CELERY_BROKER_URL"]    = "memory://"
    os.environ["CELERY_RESULT_BACKEND"] = "memory://"

# Auto-configure Databricks workspace connection from injected env vars
if is_databricks:
    os.environ["DATABRICKS_AUTO_DISCOVER"] = "true"
    workspace_host  = os.getenv("DATABRICKS_SERVER_HOSTNAME") or os.getenv("DATABRICKS_HOST")
    workspace_token = os.getenv("DATABRICKS_TOKEN")
    warehouse_path  = os.getenv("DATABRICKS_HTTP_PATH")
    if workspace_host and workspace_token:
        os.environ["DATABRICKS_WORKSPACE_HOST"]  = workspace_host
        os.environ["DATABRICKS_WORKSPACE_TOKEN"] = workspace_token
        if warehouse_path:
            os.environ["DATABRICKS_WAREHOUSE_PATH"] = warehouse_path

# ── Build the Next.js frontend ────────────────────────────────────────────────
# The frontend is built with NEXT_PUBLIC_API_URL="" so all fetch() calls use
# relative URLs (/api/v1/...) — they resolve to the same Databricks App origin,
# so the proxy sees same-origin requests and injects X-Forwarded-* headers.
frontend_dir = os.path.join(repo_root, "frontend")
frontend_out = os.path.join(frontend_dir, "out")

# Store for backend/app/main.py to pick up and serve
os.environ["FRONTEND_OUT_DIR"] = frontend_out

# Set build-time env vars before running npm (NEXT_PUBLIC_* are baked at build)
os.environ["NEXT_PUBLIC_API_URL"]          = ""      # relative URLs = same origin
os.environ["NEXT_PUBLIC_DATABRICKS_MODE"]  = "true"  # show Databricks button

# We force a rebuild on deployment to ensure UI updates are caught
if os.path.isdir(frontend_dir):
    print("🏗️  Building Next.js frontend (this runs once per fresh deployment)...")
    try:
        subprocess.run(
            ["npm", "install", "--prefer-offline", "--no-audit", "--no-fund"],
            cwd=frontend_dir, check=True
        )
        subprocess.run(
            ["npm", "run", "build"],
            cwd=frontend_dir, check=True
        )
        print("✅ Frontend build complete")
    except FileNotFoundError:
        print("⚠️  npm not found — API-only mode (frontend won't be served)")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Frontend build failed ({e}) — API-only mode")
else:
    print("⚠️  frontend/ directory not found — API-only mode")

# ── Start the FastAPI backend ─────────────────────────────────────────────────
print("=" * 60)
print("Starting DataOne (unified: API + frontend) in Databricks Apps")
print("=" * 60)
print(f"   App URL:       {backend_url}")
print(f"   Port:          {databricks_port}")
print(f"   Frontend out:  {frontend_out}")
print(f"   Frontend built: {os.path.isfile(os.path.join(frontend_out, 'index.html'))}")
print()

os.chdir(backend_path)

print(f"🚀 Starting FastAPI on port {databricks_port}...")

try:
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=databricks_port,
        log_level="info",
    )
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Failed to start: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
