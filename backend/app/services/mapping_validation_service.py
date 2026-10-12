"""Type-compatibility validation for mapping field edges.

Implements the matrix in the design spec §5. Each edge receives a verdict
``ok | lossy_warning | blocking`` and a human-readable message. The summary
returned to callers carries per-edge detail and aggregate counts.
"""
from __future__ import annotations

from typing import Any, Dict, List


# Type families used for cross-engine compatibility decisions.
_TEXT_FAMILY = {"TEXT", "VARCHAR", "CHAR", "CLOB", "STRING"}
_INT_FAMILY = {"INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT"}
_FLOAT_FAMILY = {"FLOAT", "DOUBLE", "REAL", "DECIMAL", "NUMERIC"}
_DATE_FAMILY = {"DATE"}
_TS_FAMILY = {"TIMESTAMP", "DATETIME", "TIMESTAMPTZ"}
_BOOL_FAMILY = {"BOOLEAN", "BOOL"}

# Review §11.10: plain family matching (above) treats every int-family pair
# and every float-family pair as equally safe, so a narrowing conversion
# within a family -- e.g. BIGINT -> SMALLINT, DOUBLE -> REAL -- was silently
# passing as "ok" even though the target can't hold every value the source
# can. These ranks let _is_lossy() detect "narrower than" within a family;
# same-or-widening pairs (e.g. SMALLINT -> BIGINT) are unaffected and still
# fall through to the existing same-family "ok" path.
_INT_RANK = {"TINYINT": 1, "SMALLINT": 2, "INTEGER": 3, "INT": 3, "BIGINT": 4}
_FLOAT_RANK = {"REAL": 1, "FLOAT": 2, "DOUBLE": 3, "DECIMAL": 3, "NUMERIC": 3}


def _normalize(t: Any) -> str:
    return (str(t or "")).strip().upper().split("(")[0]


def _is_narrowing_int(src: Any, tgt: Any) -> bool:
    """True when src is a wider integer type than tgt (review §11.10).

    Widening (e.g. INTEGER -> BIGINT) returns False; equal returns False.
    """
    s_n, t_n = _normalize(src), _normalize(tgt)
    s_r = _INT_RANK.get(s_n)
    t_r = _INT_RANK.get(t_n)
    if s_r is None or t_r is None:
        return False
    return s_r > t_r


def _is_narrowing_float(src: Any, tgt: Any) -> bool:
    """True when src is a wider float type than tgt (review §11.10).

    REAL < FLOAT < DOUBLE / DECIMAL / NUMERIC. Narrowing (DOUBLE -> REAL)
    is lossy; widening (REAL -> DOUBLE) returns False.
    """
    s_n, t_n = _normalize(src), _normalize(tgt)
    s_r = _FLOAT_RANK.get(s_n)
    t_r = _FLOAT_RANK.get(t_n)
    if s_r is None or t_r is None:
        return False
    return s_r > t_r


def _family(t: Any) -> str:
    n = _normalize(t)
    if n in _TEXT_FAMILY:
        return "text"
    if n in _INT_FAMILY:
        return "int"
    if n in _FLOAT_FAMILY:
        return "float"
    if n in _DATE_FAMILY:
        return "date"
    if n in _TS_FAMILY:
        return "timestamp"
    if n in _BOOL_FAMILY:
        return "bool"
    return "other"


def _is_lossless_widening(src: Any, tgt: Any) -> bool:
    s, t = _normalize(src), _normalize(tgt)
    if s == "INTEGER" and t == "BIGINT":
        return True
    if _family(s) == "text" and _family(t) == "text":
        return True
    if s == "DATE" and t == "TIMESTAMP":
        return True
    if _family(s) == "bool" and _family(t) in {"int", "text"}:
        return True
    if s == t:
        return True
    return False


def _is_incompatible(src: Any, tgt: Any) -> bool:
    s, t = _family(src), _family(tgt)
    if s == "text" and t == "int":
        return True
    if s == "text" and t == "float":
        return True
    if s == "text" and t == "bool":
        return True
    if s == "text" and t == "date":
        return True
    if s == "text" and t == "timestamp":
        return True
    return False


def _has_null_safety(transform: Dict[str, Any]) -> bool:
    kind = (transform or {}).get("kind")
    return kind in {"default", "coalesce", "null_if", "cast"}


def _is_lossy(src: Any, tgt: Any) -> bool:
    s, t = _family(src), _family(tgt)
    if s == "int" and t == "text":
        return True
    if s == "float" and t == "int":
        return True
    if s == "float" and t == "text":
        return True
    if s == "timestamp" and t == "date":
        return True
    if s == "int" and t == "float":
        return True
    # Within-family narrowing (review §11.10). Same-or-widening pairs
    # (e.g. INTEGER -> BIGINT) stay "ok" via _is_lossless_widening;
    # narrowing pairs (BIGINT -> SMALLINT, DOUBLE -> REAL) are lossy
    # because the destination cannot represent every value of the source.
    if s == "int" and t == "int" and _is_narrowing_int(src, tgt):
        return True
    if s == "float" and t == "float" and _is_narrowing_float(src, tgt):
        return True
    # Narrowing within the same family -- not visible to the family-only
    # checks above, since both sides collapse to "int" / "float".
    s_n, t_n = _normalize(src), _normalize(tgt)
    if s_n in _INT_RANK and t_n in _INT_RANK and _INT_RANK[s_n] > _INT_RANK[t_n]:
        return True
    if s_n in _FLOAT_RANK and t_n in _FLOAT_RANK and _FLOAT_RANK[s_n] > _FLOAT_RANK[t_n]:
        return True
    return False


class MappingValidationService:
    @staticmethod
    def validate_edge(edge: Dict[str, Any]) -> Dict[str, str]:
        """Validate a single edge dict. Returns ``{verdict, message}``."""
        target = edge.get("target") or {}
        sources = edge.get("sources") or []
        transformation = edge.get("transformation") or {"kind": "direct"}

        if not sources:
            return {"verdict": "blocking", "message": "edge has no source columns"}

        tgt_type = target.get("type") or ""
        tgt_nullable = target.get("nullable")
        tgt_is_pk = bool(target.get("primary_key"))

        # Block many-to-one violation at the target column level (PK).
        if tgt_is_pk and len(sources) > 1:
            return {
                "verdict": "blocking",
                "message": "primary key target cannot have multiple sources",
            }

        verdict = "ok"
        message = "compatible"
        has_cast = (transformation or {}).get("kind") == "cast"
        for src in sources:
            src_type = src.get("type") or ""
            if _is_incompatible(src_type, tgt_type):
                if not has_cast:
                    verdict = "blocking"
                    message = (
                        f"incompatible: cannot map {src_type} to {tgt_type} "
                        f"without cast"
                    )
                    break
                # With cast: the incompatibility is resolved; treat as ok.
            elif _is_lossy(src_type, tgt_type):
                # Lossy conversions are a WARNING, not a blocking error
                # (review §11.2 / FR7). Common safe operations like
                # INTEGER→TEXT or FLOAT→INTEGER should be publishable with
                # a visible warning, not silently blocked. Incompatible
                # types above already block; null-safety below can escalate
                # this to blocking if the target is NOT NULL and the source
                # is nullable.
                if not has_cast and verdict == "ok":
                    verdict = "lossy_warning"
                    message = (
                        f"lossy: mapping {src_type} to {tgt_type} may lose "
                        f"precision; add a CAST transformation to acknowledge, "
                        f"or leave as a warning"
                    )
                # With cast: the cast handles the lossy conversion; ok.
            elif _is_lossless_widening(src_type, tgt_type):
                pass
            else:
                # Same family, same-or-widening rank (e.g. SMALLINT ->
                # BIGINT, REAL -> DOUBLE) -- narrowing within a family is
                # caught by _is_lossy above, so anything reaching here is
                # genuinely safe.
                pass

        # Null-safety: target NOT NULL + nullable source without null-handling.
        # This escalates a lossy_warning to blocking (because the nullable
        # source would silently write a NULL into a NOT NULL column), but
        # never downgrades a blocking verdict.
        if tgt_nullable is False or tgt_nullable == 0:
            for src in sources:
                if src.get("nullable") and not _has_null_safety(transformation):
                    verdict = "blocking"
                    message = (
                        "target is NOT NULL but source is nullable and no "
                        "null-handling transform provided"
                    )
                    break

        return {"verdict": verdict, "message": message}

    @classmethod
    def validate_mapping(cls, mapping: Any) -> Dict[str, Any]:
        """Validate every edge in a mapping and return an aggregate summary.

        ``mapping`` may be a Mapping ORM instance or a plain dict with an
        ``edges`` key whose edges match the FieldMapping serialization shape.
        """
        if isinstance(mapping, dict):
            edges = mapping.get("edges") or []
            mapping_id = mapping.get("id")
        else:
            mapping_id = getattr(mapping, "id", None)
            # A mapping that has been revised after a prior publish (E13-6)
            # carries both its historical, version-pinned edges and the
            # current mutable draft in the same `.edges` relationship —
            # only the draft (version_id is None) is "the mapping being
            # validated"; historical edges already passed validation at
            # their own publish time and must not be re-counted here.
            edges = [
                e for e in (getattr(mapping, "edges", []) or [])
                if getattr(e, "version_id", None) is None
            ]

        issues: List[Dict[str, Any]] = []
        ok = warn = blocking = 0
        for edge in edges:
            edge_dict = _edge_to_dict(edge)
            result = cls.validate_edge(edge_dict)
            verdict = result["verdict"]
            if verdict == "ok":
                ok += 1
            elif verdict == "lossy_warning":
                warn += 1
            else:
                blocking += 1
            issues.append({
                "edge_id": edge_dict.get("id"),
                "verdict": verdict,
                "message": result["message"],
            })

        return {
            "mapping_id": mapping_id,
            "ok_count": ok,
            "warning_count": warn,
            "blocking_count": blocking,
            "issues": issues,
        }


def _edge_to_dict(edge: Any) -> Dict[str, Any]:
    if isinstance(edge, dict):
        return edge
    return {
        "id": getattr(edge, "id", None),
        "target": {
            "table": getattr(edge, "target_table", None),
            "column": getattr(edge, "target_column", None),
            "type": getattr(edge, "target_type", None),
            "nullable": (bool(getattr(edge, "target_nullable"))
                         if getattr(edge, "target_nullable") is not None else None),
            "primary_key": bool(getattr(edge, "target_is_pk", 0)),
        },
        "sources": list(getattr(edge, "sources", []) or []),
        "transformation": getattr(edge, "transformation", {}) or {},
    }
