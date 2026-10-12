"""Celery task registration test (review §11.1 / §10).

The §11.1 bug was a silent runtime failure: the worker never received the
``suggest_mappings_task`` because ``app.workers.mapping_tasks`` was missing
from the Celery ``include`` list. No existing test would have caught it
because the test conftest sets ``CELERY_TASK_ALWAYS_EAGER=True`` which makes
``send_task`` execute synchronously in-process, bypassing the broker/worker
where the real bug lives.

This test asserts the task is registered with the Celery app object after
``app.core.celery_app`` is imported the same way the worker entrypoint does.
A future addition that forgets to add a new module to ``include`` would fail
this test immediately.
"""
import pytest


def test_ai_suggestion_task_is_registered():
    """The mapping-suggest task must appear in celery_app.tasks after import.

    Mirrors the worker entrypoint: ``celery -A app.core.celery_app worker``.
    If a future change drops ``app.workers.mapping_tasks`` from the
    ``include`` list, this test fails at collection time.
    """
    # Importing the celery_app module is what the worker does on startup;
    # it's also what triggers task discovery via the include list.
    from app.core.celery_app import celery_app  # noqa: F401

    assert "app.workers.mapping_tasks.suggest_mappings_task" in celery_app.tasks, (
        "suggest_mappings_task is not registered with the Celery app. "
        "Add 'app.workers.mapping_tasks' to celery_app.py's include list. "
        "Without this, every 'Get AI Suggestions' click in production "
        "silently does nothing (FR4 / FR5 / AC2 broken)."
    )


def test_ai_tasks_module_is_still_registered():
    """Regression guard: the pre-existing ai_tasks module must stay registered."""
    from app.core.celery_app import celery_app  # noqa: F401

    assert "app.tasks.ai_tasks.check_schema_drift_task" in celery_app.tasks, (
        "Pre-existing ai_tasks registration was removed. The schema-drift "
        "beat schedule depends on this task."
    )


def test_request_suggestions_uses_task_object_not_send_task_string():
    """The §11.1 review also flagged send_task-by-name as a coupling risk.

    A typo or rename of the task module silently produces a runtime failure
    when ``send_task`` is used by string. Using ``task.delay()`` from a direct
    import fails at import time instead. This test guards against regressing
    back to ``send_task``.
    """
    import inspect

    from app.services import mapping_service as mapping_service_module

    # Check the module source, not just the method body — the import lives
    # at module scope.
    module_source = inspect.getsource(mapping_service_module)
    method_source = inspect.getsource(mapping_service_module.MappingService.request_suggestions)

    assert "send_task" not in method_source, (
        "MappingService.request_suggestions should call .delay() on an "
        "imported task object, not celery_app.send_task by string name."
    )
    assert "from app.workers.mapping_tasks import" in module_source, (
        "request_suggestions must import the task object so a rename fails "
        "at import time."
    )
