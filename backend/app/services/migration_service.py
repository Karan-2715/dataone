import os
import requests
import uuid
import logging
from datetime import datetime
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from app.models.mapping import MappingRun

logger = logging.getLogger(__name__)

class MigrationService:
    @staticmethod
    def get_databricks_token() -> tuple[str, str]:
        host = os.environ.get("DATABRICKS_HOST")
        client_id = os.environ.get("DATABRICKS_CLIENT_ID")
        client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET")
        if host and client_id and client_secret:
            resp = requests.post(
                f"https://{host.rstrip('/')}/oidc/v1/token",
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials", "scope": "all-apis"},
                timeout=10
            )
            if resp.status_code == 200:
                return host, resp.json()["access_token"]
            raise Exception("Failed to get Databricks OAuth token")
        return os.environ.get("DATABRICKS_WORKSPACE_URL"), os.environ.get("DATABRICKS_ACCESS_TOKEN")

    @classmethod
    def _ensure_runner_uploaded(cls, host: str, token: str):
        # Auto-upload migration_runner.py to Databricks Workspace
        url = f"https://{host.rstrip('/')}/api/2.0/workspace/import"
        headers = {"Authorization": f"Bearer {token}"}
        
        try:
            import base64
            import os
            
            # Ensure the parent directory exists
            mkdir_url = f"https://{host.rstrip('/')}/api/2.0/workspace/mkdirs"
            requests.post(mkdir_url, headers=headers, json={"path": "/Shared/dataone"}, timeout=10)
            
            # Locate migration_runner.py relative to the backend root (it's in the project root)
            runner_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "migration_runner.py")
            with open(runner_path, "rb") as f:
                content = base64.b64encode(f.read()).decode("utf-8")
                
            payload = {
                "path": "/Shared/dataone/migration_runner",
                "format": "SOURCE",
                "language": "PYTHON",
                "content": content,
                "overwrite": True
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            if not resp.ok:
                logger.warning(f"Failed to auto-upload runner notebook: {resp.text}")
        except Exception as e:
            logger.warning(f"Exception auto-uploading runner notebook: {e}")

    @classmethod
    def trigger_migration(cls, db: Session, mapping_id: int, user_email: str, transformation_code: str = None, patches: List[Dict[str, Any]] = None) -> MappingRun:
        from app.models.mapping import MappingVersion
        import json
        published_version = db.query(MappingVersion).filter(
            MappingVersion.mapping_id == mapping_id,
            MappingVersion.status == "published"
        ).first()
        if not published_version:
            # Auto-publish the mapping if not already published.
            # This correctly handles auto-accepting suggestions and creating the MappingVersion.
            from app.services.mapping_service import MappingService
            try:
                published_version = MappingService.publish(db, mapping_id, actor=user_email)
            except Exception as e:
                from fastapi import HTTPException
                raise HTTPException(status_code=400, detail=f"Cannot run migration: Auto-publish failed: {str(e)}")


        run_token = str(uuid.uuid4())
        
        run = MappingRun(
            mapping_id=mapping_id,
            run_token=run_token,
            state="queued",
            started_at=datetime.utcnow()
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        # Trigger notebook job in Databricks
        host, token = cls.get_databricks_token()
        cls._ensure_runner_uploaded(host, token)
        
        url = f"https://{host.rstrip('/')}/api/2.1/jobs/runs/submit"
        
        payload = {
            "run_name": f"DataOne_Migration_{mapping_id}_Run_{run.id}",
            "tasks": [
                {
                    "task_key": "migrate",
                    "notebook_task": {
                        "notebook_path": "/Shared/dataone/migration_runner",
                        "base_parameters": {
                            "mapping_id": str(mapping_id),
                            "run_id": str(run.id),
                            "run_token": run_token,
                            "api_url": os.environ.get("DATABRICKS_APP_URL", "http://localhost:8000"),
                            "app_name": os.environ.get("DATABRICKS_APP_NAME", "dataonenew"),
                            "transformation_code": transformation_code or "",
                            "patches_json": json.dumps(patches) if patches else "[]"
                        }
                    },
                    "environment_key": "default"
                }
            ],
            "environments": [{
                "environment_key": "default",
                "spec": {
                    "client": "2",
                    "dependencies": []
                }
            }]
        }
        
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
            if not resp.ok:
                logger.error(f"Databricks API Error: {resp.text}")
            resp.raise_for_status()
            data = resp.json()
            run.databricks_run_id = str(data.get("run_id"))
            db.commit()
        except Exception as e:
            logger.error(f"Failed to submit Databricks run: {e}")
            run.state = "failed"
            run.error = str(e)
            run.finished_at = datetime.utcnow()
            db.commit()
            
        return run

    @classmethod
    def update_run_status(cls, db: Session, run_id: int, token: str, state: str, rows_read: int = None, rows_written: int = None, error: str = None):
        run = db.query(MappingRun).filter(MappingRun.id == run_id).first()
        if not run or run.run_token != token:
            raise Exception("Invalid run token")
        
        run.state = state
        if rows_read is not None: run.rows_read = rows_read
        if rows_written is not None: run.rows_written = rows_written
        if error: run.error = error
        
        if state in ["succeeded", "failed"]:
            run.finished_at = datetime.utcnow()
            
        db.commit()
        db.refresh(run)
        return run
