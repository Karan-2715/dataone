"""Router-level tests for GET /api/v1/schema/graph — the Schema Topology
endpoint. Covers the full wiring (router -> _get_real_table_mappings ->
DiffService.generate_graph_data) plus the AI-match noise filter."""
from fastapi.testclient import TestClient

from app.core import database as db_module
from app.main import app
from app.services.ai_service import AIService


def _client(db):
    def _override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[db_module.get_db] = _override_db
    return TestClient(app)


def test_graph_endpoint_uses_real_mapping_not_just_exact_name(
    db, seeded_mapping_with_field_mappings, monkeypatch,
):
    m, v = seeded_mapping_with_field_mappings
    # No live Ollama in tests — fall back deterministically rather than
    # depending on network reachability.
    monkeypatch.setattr(AIService, "match_schemas", staticmethod(
        lambda **kwargs: {"matches": []},
    ))

    client = _client(db)
    try:
        resp = client.get(f"/api/v1/schema/graph?source_id={m.source_id}&target_id={m.target_id}")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()

    users_node = next(n for n in body["nodes"] if n["id"] == "src_users")
    assert users_node["has_issues"] is False
    assert users_node["lineage_confirmed"] is True
    customers_node = next(n for n in body["nodes"] if n["id"] == "tgt_customers")
    assert customers_node["lineage_confirmed"] is True

    leads_node = next(n for n in body["nodes"] if n["id"] == "src_leads")
    assert leads_node["has_issues"] is True

    mapped_edge = next(e for e in body["edges"] if e["type"] == "published_mapping")
    assert mapped_edge == {
        "source": "src_users", "target": "tgt_customers", "type": "published_mapping",
        "edge_type": "business_rule", "confidence": None,
        "label": "Mapped (3 fields)", "animated": True, "style": {"stroke": "#7c3aed"},
    }
    assert body["summary"]["matched_tables"] == 1
    assert body["summary"]["missing_in_target"] == 1


def test_graph_endpoint_filters_low_confidence_and_null_target_ai_matches(
    db, seeded_mapping_with_field_mappings, monkeypatch,
):
    m, v = seeded_mapping_with_field_mappings
    call = {}

    def _match(**kwargs):
        call.update(kwargs)
        return {
            "matches": [
                {"source": "company", "target": None, "confidence": 10},
                {"source": "id", "target": "cust_id", "confidence": 5},
                {"source": "id", "target": "cust_id", "confidence": 90},
            ],
        }

    monkeypatch.setattr(AIService, "match_schemas", staticmethod(_match))

    client = _client(db)
    try:
        resp = client.get(f"/api/v1/schema/graph?source_id={m.source_id}&target_id={m.target_id}")
    finally:
        app.dependency_overrides.clear()

    body = resp.json()
    ai_edges = [e for e in body["edges"] if e["type"] == "ai_match"]
    assert len(ai_edges) == 1
    assert "90" in ai_edges[0]["label"]
    assert call["allow_llm"] is False


def test_graph_endpoint_rejects_soft_deleted_connection(
    db, physical_sqlite_connections,
):
    source, target = physical_sqlite_connections
    source.is_deleted = True
    db.commit()
    client = _client(db)
    try:
        resp = client.get(f"/api/v1/schema/graph?source_id={source.id}&target_id={target.id}")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404


def test_graph_scores_every_unmapped_source_table(
    db, physical_sqlite_connections, monkeypatch,
):
    source, target = physical_sqlite_connections
    calls = []

    def _match(**kwargs):
        calls.append((kwargs["source_name"], kwargs["target_name"]))
        return {"matches": [{
            "source": kwargs["source_schema"][0]["name"],
            "target": kwargs["target_schema"][0]["name"],
            "confidence": 80,
        }]}

    monkeypatch.setattr(AIService, "match_schemas", staticmethod(_match))
    client = _client(db)
    try:
        body = client.get(
            f"/api/v1/schema/graph?source_id={source.id}&target_id={target.id}"
        ).json()
    finally:
        app.dependency_overrides.clear()

    assert {edge["source"] for edge in body["edges"] if edge["type"] == "ai_match"} == {
        "src_users", "src_leads",
    }
    assert {source_name for source_name, _ in calls} == {"users", "leads"}
