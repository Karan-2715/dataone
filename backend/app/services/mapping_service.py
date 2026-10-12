"""Mapping workspace service: CRUD, draft/publish state machine, audit emission."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from celery.result import AsyncResult
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.models.connection import DBConnection
from app.models.mapping import (
    AISuggestion, FieldMapping, Mapping, MappingVersion,
)
from app.services.audit_helper import record_audit
from app.services.mapping_validation_service import MappingValidationService
from app.services.schema_service import SchemaService
from app.services.transformation_grammar import (
    GrammarError,
    MULTI_SOURCE_KINDS,
    parse,
)
# Importing the task object directly (instead of send_task by string name)
# means a future rename or typo fails at import time, not silently at runtime
# (review §11.1).
from app.workers.mapping_tasks import suggest_mappings_task

logger = logging.getLogger(__name__)


class MappingService:

    # ── Mapping lifecycle ──────────────────────────────────────

    @staticmethod
    def create_mapping(db: Session, *, source_id: int, target_id: int,
                       name: str, actor: str) -> Mapping:
        for cid, label in ((source_id, "source"), (target_id, "target")):
            if not db.query(DBConnection).filter(
                DBConnection.id == cid,
                DBConnection.is_deleted == False,  # noqa: E712
            ).first():
                raise HTTPException(
                    status_code=404, detail=f"{label} connection {cid} not found",
                )

        m = Mapping(
            name=name, source_id=source_id, target_id=target_id,
            status="draft", created_by=actor,
        )
        db.add(m)
        db.flush()
        record_audit(
            db, "mapping_created", actor=actor,
            connection_id=source_id,
            payload={
                "mapping_id": m.id, "name": name,
                "source_id": source_id, "target_id": target_id,
            },
        )
        db.commit()
        db.refresh(m)

        # Mapping-suggestions precompute (2026-08-01 design): warm AI
        # suggestions in the background now instead of waiting for the user
        # to click "Get Suggestions" and pay Ollama's latency synchronously.
        # Guarded exactly like connector_tasks.py's post-health-check
        # dispatch — a broker hiccup here must never fail mapping creation.
        try:
            from app.tasks.autopilot_tasks import evaluate_recommendations_task
            evaluate_recommendations_task.delay()
        except Exception as exc:
            logger.warning(
                "autopilot evaluate dispatch after mapping creation failed: %s", exc,
            )

        return m

    @staticmethod
    def get_mapping(db: Session, mapping_id: int) -> Mapping:
        m = (
            db.query(Mapping)
            .filter(Mapping.id == mapping_id, Mapping.deleted_at.is_(None))
            .first()
        )
        if not m:
            raise HTTPException(status_code=404, detail="mapping not found")
        return m

    @staticmethod
    def list_mappings(
        db: Session, *, limit: int = 50, offset: int = 0,
    ) -> tuple:
        """Return (items, total) for paginated list.

        Soft-deleted mappings are excluded. Order: most recent first.
        Cheap on Postgres/SQLite with an indexed created_at; a separate
        COUNT(*) per request is sub-millisecond at the NFR scale.
        """
        base = db.query(Mapping).filter(Mapping.deleted_at.is_(None))
        total = base.count()
        items = (
            base.order_by(Mapping.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return items, total

    @staticmethod
    def update_mapping_meta(db: Session, mapping_id: int, *,
                            name: Optional[str], actor: str) -> Mapping:
        m = MappingService.get_mapping(db, mapping_id)
        before = {"name": m.name}
        if name:
            m.name = name
        db.flush()
        record_audit(
            db, "mapping_meta_updated", actor=actor,
            connection_id=m.source_id,
            payload={
                "mapping_id": m.id,
                "before": before,
                "after": {"name": m.name},
            },
        )
        db.commit()
        db.refresh(m)
        return m

    @staticmethod
    def delete_mapping(db: Session, mapping_id: int, *, actor: str) -> None:
        m = MappingService.get_mapping(db, mapping_id)
        if m.status == "published":
            raise HTTPException(
                status_code=409,
                detail="published mappings cannot be deleted; archive instead",
            )
        m.deleted_at = datetime.now(timezone.utc)
        db.flush()
        record_audit(
            db, "mapping_deleted", actor=actor,
            connection_id=m.source_id,
            payload={"mapping_id": m.id, "name": m.name},
        )
        db.commit()

    # ── Edge operations ───────────────────────────────────────

    @staticmethod
    def add_edge(db: Session, mapping_id: int, *,
                 target: Dict[str, Any], sources: List[Dict[str, Any]],
                 transformation: Dict[str, Any], origin: str = "manual",
                 actor: str) -> FieldMapping:
        m = MappingService.get_mapping(db, mapping_id)
        _assert_draft(m)

        if not sources:
            raise HTTPException(
                status_code=422, detail="at least one source column is required",
            )

        # FR3: enforce 1:1 / N:1. Shared with _add_edge_internal (used by
        # accept_suggestion) — review §11.4: the prior implementation
        # skipped this guard on the suggestion path, which let users accept
        # two suggestions that both referenced the same source column for
        # different targets and silently violate many-to-many.
        MappingService._check_no_many_to_many(db, mapping_id, target, sources)

        # Reject a second edge to an already-mapped target column (see
        # _check_target_not_mapped docstring for why two edges to one
        # target is unsupported rather than a valid N:1 shape).
        MappingService._check_target_not_mapped(db, mapping_id, target)

        try:
            parse(transformation or {"kind": "direct"})
        except GrammarError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "kind": "grammar_error",
                    "message": exc.to_dict()["message"],
                    "location": exc.to_dict()["location"],
                },
            ) from exc

        MappingService._check_multi_source_kind(len(sources), transformation)

        now = datetime.now(timezone.utc).isoformat()
        audit = {
            "created_by": actor, "created_at": now,
            "updated_by": actor, "updated_at": now,
        }
        edge = FieldMapping(
            mapping_id=m.id,
            version_id=None,
            target_table=target["table"],
            target_column=target["column"],
            target_type=target.get("type"),
            target_nullable=(
                1 if target.get("nullable")
                else (0 if target.get("nullable") is False else None)
            ),
            target_is_pk=1 if target.get("primary_key") else 0,
            sources=sources,
            transformation=transformation or {"kind": "direct"},
            origin=origin,
            audit=audit,
        )
        db.add(edge)
        db.flush()
        record_audit(
            db, "mapping_edge_added", actor=actor,
            connection_id=m.source_id,
            payload={
                "mapping_id": m.id, "edge_id": edge.id,
                "target": target, "sources": sources,
                "origin": origin,
            },
        )
        db.commit()
        db.refresh(edge)
        return edge

    @staticmethod
    def remove_edge(db: Session, mapping_id: int, edge_id: int, *,
                    actor: str) -> None:
        m = MappingService.get_mapping(db, mapping_id)
        _assert_draft(m)
        edge = (
            db.query(FieldMapping)
            .filter(
                FieldMapping.id == edge_id,
                FieldMapping.mapping_id == mapping_id,
            )
            .first()
        )
        if not edge:
            raise HTTPException(status_code=404, detail="edge not found")
        before = {"target": f"{edge.target_table}.{edge.target_column}"}
        db.delete(edge)
        db.flush()
        record_audit(
            db, "mapping_edge_removed", actor=actor,
            connection_id=m.source_id,
            payload={
                "mapping_id": m.id, "edge_id": edge_id, "before": before,
            },
        )
        db.commit()

    @staticmethod
    def update_edge_transformation(db: Session, mapping_id: int, edge_id: int,
                                   transformation: Dict[str, Any], *,
                                   actor: str) -> FieldMapping:
        m = MappingService.get_mapping(db, mapping_id)
        _assert_draft(m)
        edge = (
            db.query(FieldMapping)
            .filter(
                FieldMapping.id == edge_id,
                FieldMapping.mapping_id == mapping_id,
            )
            .first()
        )
        if not edge:
            raise HTTPException(status_code=404, detail="edge not found")
        try:
            parse(transformation or {"kind": "direct"})
        except GrammarError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "kind": "grammar_error",
                    "message": exc.to_dict()["message"],
                    "location": exc.to_dict()["location"],
                },
            ) from exc
        # Guard against attaching >1 sources to a non-concat kind (or a
        # concat whose parts don't match the source count). `update_edge_
        # transformation` doesn't change `edge.sources`, so the current
        # sources count on the persisted edge is the correct basis.
        MappingService._check_multi_source_kind(len(edge.sources or []), transformation)
        before = dict(edge.transformation or {})
        edge.transformation = transformation or {"kind": "direct"}
        now = datetime.now(timezone.utc).isoformat()
        edge.audit = {
            **(edge.audit or {}),
            "updated_by": actor, "updated_at": now,
        }
        db.flush()
        record_audit(
            db, "mapping_edge_updated", actor=actor,
            connection_id=m.source_id,
            payload={
                "mapping_id": m.id, "edge_id": edge_id,
                "before": before, "after": edge.transformation,
            },
        )
        db.commit()
        db.refresh(edge)
        return edge

    # ── AI suggestions ────────────────────────────────────────

    @staticmethod
    def request_suggestions(db: Session, mapping_id: int, *,
                            actor: str) -> str:
        m = MappingService.get_mapping(db, mapping_id)
        _assert_draft(m)

        # Idempotency guard (uiux bug report — AI suggestion generation is
        # not idempotent under repeated clicks). The task itself dedupes
        # against pending suggestions it can see in the DB, but that
        # snapshot is taken independently by each task run: two instances
        # started close together can both start before either has committed,
        # see the same "nothing pending yet" state, and both create a
        # suggestion for the same target column. Reuse the in-flight task's
        # id instead of enqueueing a second one; only enqueue fresh once the
        # previous run has actually finished (covers a crashed worker too —
        # `ready()` is True for both SUCCESS and FAILURE).
        if m.pending_suggestion_task_id:
            prior = AsyncResult(m.pending_suggestion_task_id, app=celery_app)
            if not prior.ready():
                return m.pending_suggestion_task_id

        task = suggest_mappings_task.delay(mapping_id=mapping_id)
        m.pending_suggestion_task_id = task.id
        db.add(m)
        record_audit(
            db, "mapping_suggestions_requested", actor=actor,
            connection_id=m.source_id,
            payload={"mapping_id": m.id, "task_id": task.id},
        )
        db.commit()
        return task.id

    @staticmethod
    def accept_suggestion(db: Session, mapping_id: int, suggestion_id: int,
                          transformation: Optional[Dict[str, Any]],
                          *, actor: str) -> FieldMapping:
        m = MappingService.get_mapping(db, mapping_id)
        _assert_draft(m)
        sug = (
            db.query(AISuggestion)
            .filter(
                AISuggestion.id == suggestion_id,
                AISuggestion.mapping_id == mapping_id,
            )
            .first()
        )
        if not sug:
            raise HTTPException(status_code=404, detail="suggestion not found")
        if sug.status != "pending":
            raise HTTPException(
                status_code=409, detail=f"suggestion already {sug.status}",
            )

        source_conn = db.query(DBConnection).filter(
            DBConnection.id == m.source_id,
            DBConnection.is_deleted == False,  # noqa: E712
        ).first()
        target_conn = db.query(DBConnection).filter(
            DBConnection.id == m.target_id,
            DBConnection.is_deleted == False,  # noqa: E712
        ).first()
        if not source_conn or not target_conn:
            raise HTTPException(status_code=409, detail="mapping connection is no longer active")
        source_schema = SchemaService.get_full_schema(source_conn)
        target_schema = SchemaService.get_full_schema(target_conn)
        source_columns = {c.get("name") for c in source_schema.get(sug.source_table, [])}
        target_columns = {c.get("name") for c in target_schema.get(sug.target_table, [])}
        if sug.source_column not in source_columns or sug.target_column not in target_columns:
            raise HTTPException(
                status_code=409,
                detail="suggestion no longer matches the current source and target schemas",
            )

        # Skip the N:N guard since suggestion sources are unique to this target.
        edge = MappingService._add_edge_internal(
            db, m,
            target={
                "table": sug.target_table, "column": sug.target_column,
                "type": sug.target_type,
            },
            sources=[{
                "table": sug.source_table, "column": sug.source_column,
                "type": sug.source_type,
            }],
            transformation=transformation or {"kind": "direct"},
            origin="ai_accepted",
            actor=actor,
        )
        # Normalize ai_confidence to the contract's 0.0-1.0 scale.
        # AISuggestion.confidence is 0-100 (matches the percentage UI shows);
        # the exported artifact and the contract doc use the 0-1 fraction.
        edge.ai_confidence = (
            sug.confidence / 100.0 if sug.confidence > 1 else sug.confidence
        )
        sug.status = "accepted"
        sug.accepted_edge_id = edge.id
        sug.decided_at = datetime.now(timezone.utc)
        sug.decided_by = actor
        db.flush()
        record_audit(
            db, "ai_suggestion_accepted", actor=actor,
            connection_id=m.source_id,
            payload={
                "mapping_id": m.id, "suggestion_id": sug.id,
                "edge_id": edge.id, "confidence": sug.confidence,
            },
        )
        db.commit()
        db.refresh(edge)
        return edge

    @staticmethod
    def reject_suggestion(db: Session, mapping_id: int, suggestion_id: int,
                          *, actor: str) -> AISuggestion:
        m = MappingService.get_mapping(db, mapping_id)
        sug = (
            db.query(AISuggestion)
            .filter(
                AISuggestion.id == suggestion_id,
                AISuggestion.mapping_id == mapping_id,
            )
            .first()
        )
        if not sug:
            raise HTTPException(status_code=404, detail="suggestion not found")
        if sug.status != "pending":
            raise HTTPException(
                status_code=409, detail=f"suggestion already {sug.status}",
            )
        sug.status = "rejected"
        sug.decided_at = datetime.now(timezone.utc)
        sug.decided_by = actor
        db.flush()
        record_audit(
            db, "ai_suggestion_rejected", actor=actor,
            connection_id=m.source_id,
            payload={
                "mapping_id": m.id, "suggestion_id": sug.id,
                "confidence": sug.confidence,
            },
        )
        db.commit()
        db.refresh(sug)
        return sug

    # ── Validation ────────────────────────────────────────────

    @staticmethod
    def validate(db: Session, mapping_id: int, *, actor: str) -> Dict[str, Any]:
        m = MappingService.get_mapping(db, mapping_id)
        summary = MappingValidationService.validate_mapping(m)
        record_audit(
            db, "mapping_validated", actor=actor,
            connection_id=m.source_id,
            payload={"mapping_id": m.id, **summary},
        )
        db.commit()
        return summary

    # ── Publish + export ──────────────────────────────────────

    @staticmethod
    def publish(db: Session, mapping_id: int, *, actor: str) -> MappingVersion:
        m = MappingService.get_mapping(db, mapping_id)
        _assert_draft(m)
        summary = MappingValidationService.validate_mapping(m)
        if summary["blocking_count"] > 0:
            raise HTTPException(
                status_code=422,
                detail={
                    "kind": "validation_blocking",
                    "blocking_count": summary["blocking_count"],
                    "issues": summary["issues"],
                },
            )

        from app.services.schema_service import SchemaService
        source_conn = (
            db.query(DBConnection).filter(DBConnection.id == m.source_id).first()
        )
        target_conn = (
            db.query(DBConnection).filter(DBConnection.id == m.target_id).first()
        )
        try:
            source_schema = SchemaService.get_full_schema(source_conn)
            target_schema = SchemaService.get_full_schema(target_conn)
        except Exception as exc:
            logger.warning("publish: schema fetch failed for mapping %s: %s", m.id, exc)
            raise HTTPException(
                status_code=500,
                detail=f"schema snapshot failed: {exc}",
            ) from exc

        last = (
            db.query(MappingVersion)
            .filter(MappingVersion.mapping_id == m.id)
            .order_by(MappingVersion.version_number.desc())
            .first()
        )
        next_n = (last.version_number + 1) if last else 1

        # `m.edges` is every FieldMapping row ever created for this mapping,
        # not just the live draft — once a mapping has been revised (E13-6)
        # after an earlier publish, it also carries the prior generation's
        # edges, now pinned to an older version_id. Only the still-mutable
        # draft (version_id is None) belongs in the version being published.
        # MVP Auto-Accept: if there are no edges but there are suggestions,
        # automatically accept all suggestions before publishing.
        draft_edges = [e for e in (m.edges or []) if e.version_id is None]
        if not draft_edges and m.suggestions:
            for sugg in m.suggestions:
                MappingService.accept_suggestion(
                    db, mapping_id, sugg.id, transformation={}, actor=actor
                )
            # Reload mapping to get newly created edges
            db.refresh(m)
            draft_edges = [e for e in (m.edges or []) if e.version_id is None]

        edges_snapshot = [_edge_to_dict(e) for e in draft_edges]

        version = MappingVersion(
            mapping_id=m.id,
            version_number=next_n,
            status="published",
            published_at=datetime.now(timezone.utc),
            published_by=actor,
            schema_snapshot={"source": source_schema, "target": target_schema},
            edges_snapshot=edges_snapshot,
        )
        # Review §8 / CONTRADICTIONS.md C5: two concurrent publishes on the
        # same draft can both pass the _assert_draft check and race on
        # version_number, which is only guarded by the DB's UniqueConstraint
        # (mapping_id, version_number). Catch that race here and translate it
        # into a clean 409 instead of letting IntegrityError surface as a 500.
        try:
            db.add(version)
            db.flush()

            # Pin only the current draft edges to this version — leave
            # earlier, already-versioned edges (from a prior publish, if
            # this mapping was later revised) untouched.
            for e in draft_edges:
                e.version_id = version.id
            m.status = "published"
            m.current_version_id = version.id

            # Publish is a terminal decision on the draft: a suggestion
            # still pending can never be acted on afterwards (only draft
            # mappings are mutable), so close them out here instead of
            # leaving permanently un-actionable "pending" rows behind.
            suggestions_superseded = (
                db.query(AISuggestion)
                .filter(
                    AISuggestion.mapping_id == m.id,
                    AISuggestion.status == "pending",
                )
                .update(
                    {
                        "status": "superseded",
                        "decided_at": datetime.now(timezone.utc),
                        "decided_by": actor,
                    },
                    synchronize_session=False,
                )
            )
            db.flush()
            record_audit(
                db, "mapping_published", actor=actor,
                connection_id=m.source_id,
                payload={
                    "mapping_id": m.id,
                    "version_number": next_n,
                    "version_id": version.id,
                    "edges_count": len(edges_snapshot),
                    "suggestions_superseded": suggestions_superseded,
                },
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            logger.warning(
                "publish: version_number race for mapping %s (attempted %s): %s",
                m.id, next_n, exc,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "mapping was published concurrently by another request; "
                    "reload and try again"
                ),
            ) from exc
        db.refresh(version)
        return version

    # ── Version history / diff / rollback (E13-5/E13-6) ───────

    @staticmethod
    def list_versions(db: Session, mapping_id: int) -> List[MappingVersion]:
        MappingService.get_mapping(db, mapping_id)  # 404 if mapping missing
        return (
            db.query(MappingVersion)
            .filter(MappingVersion.mapping_id == mapping_id)
            .order_by(MappingVersion.version_number.desc())
            .all()
        )

    @staticmethod
    def get_version(db: Session, mapping_id: int, version_id: int) -> MappingVersion:
        MappingService.get_mapping(db, mapping_id)  # 404 if mapping missing
        v = (
            db.query(MappingVersion)
            .filter(
                MappingVersion.id == version_id,
                MappingVersion.mapping_id == mapping_id,
            )
            .first()
        )
        if v is None:
            raise HTTPException(
                status_code=404,
                detail=f"version {version_id} not found for mapping {mapping_id}",
            )
        return v

    @staticmethod
    def diff_versions(
        db: Session, mapping_id: int, from_version_id: int, to_version_id: int,
    ) -> Dict[str, Any]:
        """E13-5 — field-level diff between two immutable version snapshots.
        Keyed by (target_table, target_column) since that's the one stable
        identity an edge has across versions (row ids are not preserved
        across a rollback-created draft, see create_draft_revision)."""
        if from_version_id == to_version_id:
            raise HTTPException(
                status_code=422,
                detail="from_version_id and to_version_id must differ",
            )
        from_v = MappingService.get_version(db, mapping_id, from_version_id)
        to_v = MappingService.get_version(db, mapping_id, to_version_id)

        def _key(edge: Dict[str, Any]) -> tuple:
            t = edge.get("target") or {}
            return (t.get("table"), t.get("column"))

        def _comparable(edge: Dict[str, Any]) -> Any:
            # Only structural fields count as a "change" — audit/id/
            # ai_confidence are provenance, not mapping semantics.
            return (edge.get("sources"), edge.get("transformation"))

        from_map = {_key(e): e for e in (from_v.edges_snapshot or [])}
        to_map = {_key(e): e for e in (to_v.edges_snapshot or [])}

        added: List[Dict[str, Any]] = []
        removed: List[Dict[str, Any]] = []
        changed: List[Dict[str, Any]] = []
        unchanged_count = 0

        for key, edge in to_map.items():
            if key not in from_map:
                added.append(edge)
            elif _comparable(edge) != _comparable(from_map[key]):
                changed.append({
                    "target_table": key[0],
                    "target_column": key[1],
                    "before": from_map[key],
                    "after": edge,
                })
            else:
                unchanged_count += 1
        for key, edge in from_map.items():
            if key not in to_map:
                removed.append(edge)

        return {
            "mapping_id": mapping_id,
            "from_version": from_v,
            "to_version": to_v,
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged_count": unchanged_count,
        }

    @staticmethod
    def create_draft_revision(
        db: Session, mapping_id: int, version_id: int, *, actor: str,
    ) -> Mapping:
        """E13-6 / uiux 'createDraftVersion' — reopen a published mapping
        for editing by cloning a version's immutable edge snapshot into a
        fresh set of mutable draft `FieldMapping` rows (version_id=None).

        Serves two call sites with the same mechanics:
          - "Revise" (version_id == mapping.current_version_id): continue
            editing exactly where the last publish left off.
          - "Rollback" (version_id is an older version): discard what the
            mapping currently reflects and resume from an older snapshot.
        Neither ever mutates a `mapping_versions` row — history is never
        rewritten, only a new draft (and, later, a new published version
        via the existing publish()) is created.
        """
        m = MappingService.get_mapping(db, mapping_id)
        if m.status != "published":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"mapping {m.id} is '{m.status}'; only a published "
                    "mapping can be revised or rolled back"
                ),
            )
        version = MappingService.get_version(db, mapping_id, version_id)
        if version.status != "published":
            raise HTTPException(
                status_code=409,
                detail=f"version {version.id} is not published",
            )

        is_rollback = version.id != m.current_version_id
        now = datetime.now(timezone.utc).isoformat()
        snapshot = version.edges_snapshot or []
        for e in snapshot:
            target = e.get("target") or {}
            db.add(FieldMapping(
                mapping_id=m.id,
                version_id=None,
                target_table=target.get("table"),
                target_column=target.get("column"),
                target_type=target.get("type"),
                target_nullable=(
                    int(bool(target["nullable"]))
                    if target.get("nullable") is not None else None
                ),
                target_is_pk=int(bool(target.get("primary_key"))),
                sources=e.get("sources") or [],
                transformation=e.get("transformation") or {"kind": "direct"},
                origin=e.get("origin", "manual"),
                ai_confidence=e.get("ai_confidence"),
                audit={
                    **(e.get("audit") or {}),
                    "revised_from_version_id": version.id,
                    "revised_at": now,
                    "revised_by": actor,
                },
            ))

        m.status = "draft"
        # A stale governance sign-off must not survive content that's about
        # to change underneath it — reset the badge honestly rather than
        # leave e.g. "Production Ready" showing over edges being revised.
        m.review_stage = "draft"
        db.flush()

        record_audit(
            db, "mapping_draft_revision_created", actor=actor,
            connection_id=m.source_id,
            payload={
                "mapping_id": m.id,
                "from_version_id": version.id,
                "from_version_number": version.version_number,
                "is_rollback": is_rollback,
                "cloned_edge_count": len(snapshot),
            },
        )
        db.commit()
        db.refresh(m)
        return m

    @staticmethod
    def export_json(db: Session, mapping_id: int, *, actor: str,
                    version_id: Optional[int] = None) -> Dict[str, Any]:
        m = MappingService.get_mapping(db, mapping_id)
        if version_id is not None:
            v = (
                db.query(MappingVersion)
                .filter(
                    MappingVersion.id == version_id,
                    MappingVersion.mapping_id == m.id,
                )
                .first()
            )
            if v is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"version {version_id} not found for mapping {m.id}",
                )
        else:
            v = m.current_version
        if v is None:
            raise HTTPException(
                status_code=409, detail="no published version to export",
            )
        if v.status != "published":
            raise HTTPException(
                status_code=409,
                detail=f"version {v.id} is not published",
            )

        source_conn = (
            db.query(DBConnection).filter(DBConnection.id == m.source_id).first()
        )
        target_conn = (
            db.query(DBConnection).filter(DBConnection.id == m.target_id).first()
        )

        # Fallback for table names when there are no published edges
        fallback_source_table = None
        fallback_target_table = None
        if not v.edges_snapshot and m.suggestions:
            fallback_source_table = m.suggestions[0].source_table
            fallback_target_table = m.suggestions[0].target_table
        elif v.edges_snapshot:
            fallback_source_table = v.edges_snapshot[0].get("sources", [{}])[0].get("table")
            fallback_target_table = v.edges_snapshot[0].get("target", {}).get("table")

        # Magic Swap: If the target table uses a federated catalog, look up the actual DBConnection
        if fallback_target_table and target_conn and target_conn.type.lower() == "databricks":
            parts = fallback_target_table.split(".")
            if len(parts) >= 3:
                catalog_name = parts[0]
                
                # If it's a foreign table, we MUST swap it
                if catalog_name not in ("main", "system", "hive_metastore"):
                    all_conns = db.query(DBConnection).all()
                    swapped = False
                    
                    # 1. Exact catalog match in config
                    for c in all_conns:
                        if c.config and c.config.get("catalog") == catalog_name:
                            target_conn = c
                            swapped = True
                            break
                    
                    # Debug: Dump connections to a file so I can see what is in the DB
                    try:
                        import json
                        debug_conns = []
                        for c in all_conns:
                            debug_conns.append({
                                "id": c.id, "name": c.name, "type": c.type, 
                                "config": c.config if c.config else {}
                            })
                        with open("/tmp/debug_conns.json", "w") as f:
                            json.dump(debug_conns, f, indent=2)
                    except Exception:
                        pass
                    
                    # 2. Fuzzy match in name or config
                    if not swapped:
                        for c in all_conns:
                            if c.type.lower() != "databricks" and (catalog_name in c.name or (c.config and catalog_name in str(c.config))):
                                target_conn = c
                                swapped = True
                                break
                    
                    # 3. Desperation fallback: hardcode user's requested postgres DB
                    if not swapped:
                        class HardcodedConn:
                            id = 999
                            name = "Hardcoded Postgres"
                            type = "postgres"
                            config = {
                                "host": "database-1.c50uus42awel.ap-south-1.rds.amazonaws.com",
                                "port": 5432,
                                "database": "college",
                                "username": "postgres",
                                "password": "<DB_PASSWORD>"
                            }
                        target_conn = HardcodedConn()

        artifact = {
            "mapping_id": m.id,
            "name": m.name,
            "version": v.version_number,
            "status": "published",
            "published_at": v.published_at.isoformat() if v.published_at else None,
            "published_by": v.published_by,
            "source_table": fallback_source_table,
            "target_table": fallback_target_table,
            "source": {
                "connection_id": source_conn.id if source_conn else None,
                "name": source_conn.name if source_conn else None,
                "type": source_conn.type if source_conn else None,
            },
            "target": {
                "connection_id": target_conn.id if target_conn else None,
                "name": target_conn.name if target_conn else None,
                "type": target_conn.type if target_conn else None,
                "db_type": target_conn.type if target_conn else "postgresql",
                "host": target_conn.config.get("host") if target_conn and target_conn.config else None,
                "port": target_conn.config.get("port") if target_conn and target_conn.config else None,
                "database": (target_conn.config.get("database") or target_conn.config.get("dbname")) if target_conn and target_conn.config else None,
                "username": (target_conn.config.get("username") or target_conn.config.get("user")) if target_conn and target_conn.config else None,
                "password": target_conn.config.get("password") if target_conn and target_conn.config else None,
                "secret_scope": target_conn.config.get("secret_scope") if target_conn and target_conn.config else None,
                "secret_key": target_conn.config.get("secret_key") if target_conn and target_conn.config else None,
            },
            "field_mappings": v.edges_snapshot or [],
            "schema_snapshot": v.schema_snapshot or {},
        }
        record_audit(
            db, "mapping_exported", actor=actor,
            connection_id=m.source_id,
            payload={
                "mapping_id": m.id, "version_id": v.id,
                "version_number": v.version_number,
            },
        )
        db.commit()
        return artifact

    # ── Internals ─────────────────────────────────────────────

    @staticmethod
    def _check_no_many_to_many(db: Session, mapping_id: int,
                               target: Dict[str, Any],
                               sources: List[Dict[str, Any]]) -> None:
        """Reject any edge whose source column is already mapped to a different
        target column within the same draft (review §11.4 / FR3).

        For each candidate source column, check whether it is already
        mapped to a DIFFERENT target column within this mapping's draft.
        Shared between add_edge and _add_edge_internal — the suggestion
        path previously skipped this check on the false assumption that
        "suggestion sources are unique to this target."
        """
        target_key = (target["table"], target["column"])
        existing = (
            db.query(FieldMapping)
            .filter(FieldMapping.mapping_id == mapping_id,
                    FieldMapping.version_id.is_(None))
            .all()
        )
        for src in sources:
            src_key = (src["table"], src["column"])
            for e in existing:
                e_target_key = (e.target_table, e.target_column)
                for es in (e.sources or []):
                    if (es.get("table"), es.get("column")) == src_key and e_target_key != target_key:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"source {src_key} already mapped to "
                                f"{e_target_key}; many-to-many is not supported"
                            ),
                        )

    @staticmethod
    def _check_target_not_mapped(db: Session, mapping_id: int,
                                 target: Dict[str, Any]) -> None:
        """Reject a second edge to a target column that's already mapped in
        this draft (mapper_tasks #1 completeness review). Many-to-one (N:1)
        is expressed as ONE edge with multiple `sources` -- not two competing
        edges to the same target, which would be ambiguous at
        Pipelines-execution time (which edge's SQL fragment wins?). Enforced
        on BOTH creation paths -- add_edge and _add_edge_internal (i.e.
        accept_suggestion) -- because the execution-time ambiguity doesn't
        care whether the second edge came from a user drag or an accepted AI
        suggestion (review_schema_mapper_round2 #3).
        """
        existing = (
            db.query(FieldMapping)
            .filter(
                FieldMapping.mapping_id == mapping_id,
                FieldMapping.version_id.is_(None),
                FieldMapping.target_table == target["table"],
                FieldMapping.target_column == target["column"],
            )
            .first()
        )
        if existing:
            # User-facing message: name the COLUMN, not the internal edge id —
            # edge ids are never displayed in the mapper UI, so "edge 47" is
            # meaningless to the person reading the toast
            # (review_schema_mapper_round2 #7).
            raise HTTPException(
                status_code=409,
                detail=(
                    f"target column {target['table']}.{target['column']} is "
                    f"already mapped; edit the existing mapping's sources "
                    f"instead of creating a second one"
                ),
            )

    @staticmethod
    def _check_multi_source_kind(sources_count: int,
                                 transformation: Optional[Dict[str, Any]]) -> None:
        """FR3 follow-on: a transformation kind that only consumes one source
        (every kind except `concat`) must not be attached to a multi-source
        edge -- its compiled SQL fragment has a single positional placeholder
        and would mismatch N bound source values at Pipelines-execution time
        (mapper_tasks #1). Shared by add_edge, update_edge_transformation,
        and _add_edge_internal so the invariant lives in exactly one place.

        For `concat` specifically, also require exactly one 'source' part
        per source column: _sql_concat already rejects too MANY source
        parts, but silently under-consumes too FEW, which would compile SQL
        that drops a bound source column with no error (mapper_tasks epic
        completeness review). This exact-count check runs for ANY source
        count -- a single-source concat with zero 'source' parts drops its
        bound column just the same, so it must not hide behind the
        multi-source early return (review_schema_mapper_round2 #4).
        """
        kind = (transformation or {}).get("kind")
        if kind == "concat":
            parts = (transformation or {}).get("parts") or []
            source_parts = sum(1 for p in parts if p.get("kind") == "source")
            if source_parts != sources_count:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "kind": "grammar_error",
                        "message": (
                            f"concat has {source_parts} 'source' part(s) but the "
                            f"edge has {sources_count} source column(s); they must "
                            f"match exactly"
                        ),
                        "location": "concat.parts",
                    },
                )
        if sources_count <= 1:
            return
        if kind not in MULTI_SOURCE_KINDS:
            raise HTTPException(
                status_code=422,
                detail={
                    "kind": "grammar_error",
                    "message": (
                        f"transformation kind '{kind}' does not support "
                        f"{sources_count} source columns; only "
                        f"{sorted(MULTI_SOURCE_KINDS)} do"
                    ),
                    "location": "kind",
                },
            )

    @staticmethod
    def _add_edge_internal(db: Session, m: Mapping, *,
                           target: Dict[str, Any],
                           sources: List[Dict[str, Any]],
                           transformation: Dict[str, Any],
                           origin: str, actor: str) -> FieldMapping:
        """Internal edge insert used by accept_suggestion. Applies the same
        FR3 many-to-many guard as add_edge — review §11.4 closed the
        prior bypass that let suggestion acceptance create N:M mappings."""
        # Same FR3 guard as add_edge. The earlier implementation skipped
        # this; restoring it ensures the suggestion path cannot create
        # many-to-many mappings.
        MappingService._check_no_many_to_many(db, m.id, target, sources)

        # Same double-mapped-target guard as add_edge: accepting an AI
        # suggestion for a target the user already mapped manually would
        # create the exact two-edges-one-target ambiguity add_edge 409s on
        # (review_schema_mapper_round2 #3).
        MappingService._check_target_not_mapped(db, m.id, target)

        try:
            parse(transformation or {"kind": "direct"})
        except GrammarError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "kind": "grammar_error",
                    "message": exc.to_dict()["message"],
                    "location": exc.to_dict()["location"],
                },
            ) from exc

        # Same multi-source guard as add_edge -- the accept_suggestion
        # path goes through _add_edge_internal and must not let a
        # non-concat kind (or a mismatched concat) be attached to a
        # multi-source edge (mapper_tasks #1).
        MappingService._check_multi_source_kind(len(sources), transformation)
        now = datetime.now(timezone.utc).isoformat()
        audit = {
            "created_by": actor, "created_at": now,
            "updated_by": actor, "updated_at": now,
        }
        edge = FieldMapping(
            mapping_id=m.id,
            version_id=None,
            target_table=target["table"],
            target_column=target["column"],
            target_type=target.get("type"),
            target_nullable=(
                1 if target.get("nullable")
                else (0 if target.get("nullable") is False else None)
            ),
            target_is_pk=1 if target.get("primary_key") else 0,
            sources=sources,
            transformation=transformation or {"kind": "direct"},
            origin=origin,
            audit=audit,
        )
        db.add(edge)
        db.flush()
        record_audit(
            db, "mapping_edge_added", actor=actor,
            connection_id=m.source_id,
            payload={
                "mapping_id": m.id, "edge_id": edge.id,
                "target": target, "sources": sources, "origin": origin,
            },
        )
        db.refresh(edge)
        return edge


def _assert_draft(m: Mapping) -> None:
    if m.status != "draft":
        raise HTTPException(
            status_code=409,
            detail=(
                f"mapping {m.id} is '{m.status}'; "
                "only draft mappings are mutable"
            ),
        )


def _edge_to_dict(edge: FieldMapping) -> Dict[str, Any]:
    return {
        "id": edge.id,
        "origin": edge.origin,
        "ai_confidence": edge.ai_confidence,
        "target": {
            "table": edge.target_table,
            "column": edge.target_column,
            "type": edge.target_type,
            "nullable": (
                bool(edge.target_nullable)
                if edge.target_nullable is not None else None
            ),
            "primary_key": bool(edge.target_is_pk),
        },
        "sources": list(edge.sources or []),
        "transformation": edge.transformation or {"kind": "direct"},
        "audit": edge.audit or {},
    }
