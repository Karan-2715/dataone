"""Router for polling Celery task status."""

from fastapi import APIRouter, HTTPException
from celery.result import AsyncResult

from app.core.celery_app import celery_app

router = APIRouter()


@router.get("/{task_id}")
def get_task_status(task_id: str):
    """Return status (and result, if ready) for a Celery task."""
    try:
        task = AsyncResult(task_id, app=celery_app)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid task id: {exc}")

    response: Dict[str, Any] = {
        "task_id": task_id,
        "status": task.status,
        "result": task.result if task.ready() else None,
    }

    if task.state == "FAILURE":
        # Surface the exception text without crashing the endpoint.
        response["error"] = str(task.result)
        response["result"] = None

    return response
