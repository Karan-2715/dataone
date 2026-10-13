"""Push DataOne's Databricks code into the signed-in user's own workspace.

The caller's Databricks credentials (their OAuth token — never a service
principal) are used to create, in *their* workspace:

    1. Unity Catalog schemas  ``<prefix>_bronze|silver|gold`` in a catalog they pick
    2. a managed landing volume  ``<prefix>_bronze.<prefix>_landing``
    3. the DataOne notebooks (uploaded as workspace notebooks)
    4. a secret scope holding the DataOne ingest key
    5. the data-quality job (serverless notebook task, optional cron schedule)

Every step is idempotent (get-or-create / overwrite), so re-running the
installer is also how a new DataOne release is rolled out. The first failing
step stops the run; steps already completed are kept and reported.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlparse

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError, NotFound, ResourceAlreadyExists
from databricks.sdk.service.catalog import VolumeType
from databricks.sdk.service.jobs import CronSchedule, JobSettings, NotebookTask, PauseStatus, Task
from databricks.sdk.service.workspace import ImportFormat, Language

from app.core.config import settings

logger = logging.getLogger(__name__)

# Bump when anything under databricks_assets/ (or the job definition below) changes.
RELEASE = "1.0.0"

ASSETS_DIR = Path(__file__).resolve().parents[1] / "databricks_assets" / "notebooks"
NOTEBOOKS = {"dataone_data_quality": "dataone_data_quality.py"}  # workspace name -> asset file
JOB_NAME = "DataOne Data Quality"
INGEST_SECRET_KEY = "dq-ingest-key"
LAYERS = ("bronze", "silver", "gold")

_UC_IDENT = re.compile(r"^[A-Za-z0-9_]{1,128}$")
_SCOPE = re.compile(r"^[A-Za-z0-9_.\-@]{1,128}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


class InstallerError(Exception):
    """Bad input or an unusable environment (maps to HTTP 400/409 in the router)."""


@dataclass
class InstallOptions:
    catalog: str
    schema_prefix: str = "dataone"
    secret_scope: str = "dataone"
    schedule_cron: Optional[str] = None  # Quartz cron, e.g. "0 0 6 * * ?"; None = run on demand only

    def validate(self) -> None:
        if not _UC_IDENT.match(self.catalog or ""):
            raise InstallerError("catalog must be 1-128 letters, digits or underscores")
        if not _UC_IDENT.match(self.schema_prefix or ""):
            raise InstallerError("schema_prefix must be 1-128 letters, digits or underscores")
        if not _SCOPE.match(self.secret_scope or ""):
            raise InstallerError("secret_scope may only contain letters, digits and _ . - @")
        if self.schedule_cron is not None and not (0 < len(self.schedule_cron) <= 120):
            raise InstallerError("schedule_cron must be a Quartz cron expression")


@dataclass
class StepResult:
    step: str
    status: str  # created | updated | unchanged | failed
    detail: str = ""


@dataclass
class InstallOutcome:
    ok: bool
    steps: List[StepResult] = field(default_factory=list)
    job_id: Optional[int] = None
    notebook_folder: Optional[str] = None

    def as_dict(self) -> dict:
        return {"ok": self.ok, "job_id": self.job_id, "notebook_folder": self.notebook_folder,
                "steps": [asdict(s) for s in self.steps]}


def normalize_host(host: str | None) -> str | None:
    """``dbc-1.cloud.databricks.com`` / ``https://…/`` -> ``https://dbc-1.cloud.databricks.com``."""
    if not host:
        return None
    host = host.strip().rstrip("/")
    return host if host.startswith(("https://", "http://")) else f"https://{host}"


def _hint_for(exc: DatabricksError) -> str:
    text = str(exc)
    if "scope" in text.lower() and ("insufficient" in text.lower() or "required" in text.lower()):
        return f"{text} — your Databricks sign-in lacks the needed API scope; sign in with Databricks again."
    return text


class WorkspaceInstaller:
    def __init__(self, client: WorkspaceClient, user_email: str):
        self.w = client
        self.user_email = user_email  # DataOne user (audit/logging only)

    # ── public API ────────────────────────────────────────────────────────────
    def install(self, opts: InstallOptions) -> InstallOutcome:
        opts.validate()
        self._check_preconditions()
        outcome = InstallOutcome(ok=False)
        try:
            # Notebooks go in the home folder of the *Databricks* identity behind the token.
            identity = self.w.current_user.me().user_name
        except DatabricksError as exc:
            outcome.steps.append(StepResult("auth", "failed", _hint_for(exc)))
            return outcome
        folder = f"/Workspace/Users/{identity}/DataOne"
        outcome.notebook_folder = folder

        steps: list[tuple[str, Callable[[], StepResult | list[StepResult]]]] = [
            ("catalog", lambda: self._check_catalog(opts)),
            ("schemas", lambda: self._ensure_schemas(opts)),
            ("volume", lambda: self._ensure_volume(opts)),
            ("notebooks", lambda: self._upload_notebooks(folder)),
            ("secrets", lambda: self._ensure_secret(opts)),
            ("job", lambda: self._ensure_job(opts, folder, outcome)),
        ]
        for name, run in steps:
            logger.info("[pipeline] stage=workspace_install step=%s user=%s", name, self.user_email)
            try:
                result = run()
            except DatabricksError as exc:
                outcome.steps.append(StepResult(name, "failed", _hint_for(exc)))
                return outcome
            outcome.steps.extend(result if isinstance(result, list) else [result])
        outcome.ok = True
        return outcome

    def run_job_now(self, job_id: int, notebook_params: dict[str, str] | None = None) -> int:
        if notebook_params is None:
            return self.w.jobs.run_now(job_id).run_id
        return self.w.jobs.run_now(job_id, notebook_params=notebook_params).run_id

    def job_run_url(self, job_id: int, run_id: int) -> str:
        return f"{self.w.config.host.rstrip('/')}/jobs/{job_id}/runs/{run_id}"

    # ── preconditions ─────────────────────────────────────────────────────────
    @staticmethod
    def _check_preconditions() -> None:
        if not settings.DQ_INGEST_KEY:
            raise InstallerError("DQ_INGEST_KEY is not configured on the DataOne server")
        host = urlparse(settings.BACKEND_URL or "").hostname or ""
        if host in _LOCAL_HOSTS:
            raise InstallerError(
                "BACKEND_URL points at localhost; the Databricks job could not reach DataOne. "
                "Set BACKEND_URL to the public URL of this server."
            )

    # ── steps ─────────────────────────────────────────────────────────────────
    def _check_catalog(self, opts: InstallOptions) -> StepResult:
        try:
            self.w.catalogs.get(opts.catalog)
        except NotFound:
            raise _StepFailed(f"Catalog '{opts.catalog}' does not exist or you cannot see it")
        return StepResult("catalog", "unchanged", opts.catalog)

    def _ensure_schemas(self, opts: InstallOptions) -> List[StepResult]:
        results = []
        for layer in LAYERS:
            name = f"{opts.schema_prefix}_{layer}"
            full = f"{opts.catalog}.{name}"
            try:
                self.w.schemas.get(full)
                results.append(StepResult("schema", "unchanged", full))
            except NotFound:
                self.w.schemas.create(name=name, catalog_name=opts.catalog,
                                      comment=f"DataOne {layer} layer")
                results.append(StepResult("schema", "created", full))
        return results

    def _ensure_volume(self, opts: InstallOptions) -> StepResult:
        schema = f"{opts.schema_prefix}_bronze"
        name = f"{opts.schema_prefix}_landing"
        full = f"{opts.catalog}.{schema}.{name}"
        try:
            self.w.volumes.read(full)
            return StepResult("volume", "unchanged", full)
        except NotFound:
            self.w.volumes.create(catalog_name=opts.catalog, schema_name=schema, name=name,
                                  volume_type=VolumeType.MANAGED, comment="DataOne landing zone for raw files")
            return StepResult("volume", "created", full)

    def _upload_notebooks(self, folder: str) -> List[StepResult]:
        self.w.workspace.mkdirs(folder)
        results = []
        for name, filename in NOTEBOOKS.items():
            path = f"{folder}/{name}"
            content = (ASSETS_DIR / filename).read_bytes()
            try:
                self.w.workspace.get_status(path)
                existed = True
            except NotFound:
                existed = False
            self.w.workspace.upload(path, io.BytesIO(content), format=ImportFormat.SOURCE,
                                    language=Language.PYTHON, overwrite=True)
            results.append(StepResult("notebook", "updated" if existed else "created", path))
        return results

    def _ensure_secret(self, opts: InstallOptions) -> StepResult:
        try:
            self.w.secrets.create_scope(opts.secret_scope)
            created = True
        except ResourceAlreadyExists:
            created = False
        self.w.secrets.put_secret(opts.secret_scope, INGEST_SECRET_KEY, string_value=settings.DQ_INGEST_KEY)
        return StepResult("secrets", "created" if created else "updated",
                          f"{opts.secret_scope}/{INGEST_SECRET_KEY}")

    def _ensure_job(self, opts: InstallOptions, folder: str, outcome: InstallOutcome) -> StepResult:
        params = {
            "catalog": opts.catalog,
            "schemas": ",".join(f"{opts.schema_prefix}_{layer}" for layer in LAYERS),
            "backend_url": settings.BACKEND_URL.rstrip("/"),
            "secret_scope": opts.secret_scope,
            "secret_key": INGEST_SECRET_KEY,
        }
        job_settings = JobSettings(
            name=JOB_NAME,
            tasks=[Task(task_key="data_quality",
                        notebook_task=NotebookTask(notebook_path=f"{folder}/dataone_data_quality",
                                                   base_parameters=params))],
            schedule=(CronSchedule(quartz_cron_expression=opts.schedule_cron, timezone_id="UTC",
                                   pause_status=PauseStatus.UNPAUSED) if opts.schedule_cron else None),
            max_concurrent_runs=1,
        )
        existing = next(iter(self.w.jobs.list(name=JOB_NAME)), None)
        if existing is not None:
            self.w.jobs.reset(existing.job_id, job_settings)
            outcome.job_id = existing.job_id
            return StepResult("job", "updated", f"{JOB_NAME} (id {existing.job_id})")
        created = self.w.jobs.create(name=job_settings.name, tasks=job_settings.tasks,
                                     schedule=job_settings.schedule,
                                     max_concurrent_runs=job_settings.max_concurrent_runs)
        outcome.job_id = created.job_id
        return StepResult("job", "created", f"{JOB_NAME} (id {created.job_id})")


class _StepFailed(DatabricksError):
    """Internal: a step reported a clean, user-facing failure."""
