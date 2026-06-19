from __future__ import annotations

from fastapi.testclient import TestClient

from picoin_forge_l2.common.hashing import sha256_text
from picoin_forge_l2.coordinator import main as coordinator_main
from picoin_forge_l2.coordinator.audit import build_event_export, write_event_export
from picoin_forge_l2.coordinator.storage import CoordinatorStorage


def test_event_export_writes_jsonl_with_hash(tmp_path):
    storage = CoordinatorStorage(tmp_path)
    storage.record_event("worker.registered", "worker_a", {"wallet": "PIA"})
    storage.record_event("challenge.passed", "worker_a", {"challenge_id": "challenge_a"})

    output_path = tmp_path / "audit" / "events.jsonl"
    result = write_event_export(tmp_path, output_path)
    content = output_path.read_text(encoding="utf-8")

    assert result["event_count"] == 2
    assert result["export_hash"] == sha256_text(content)
    assert "worker.registered" in content
    assert "challenge.passed" in content


def test_events_export_api_returns_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    CoordinatorStorage(tmp_path).record_event("epoch.closed", "epoch-1", {"result_hash": "hash"})
    client = TestClient(coordinator_main.api)

    response = client.get("/events/export")

    assert response.status_code == 200
    body = response.json()
    assert body["event_count"] == 1
    assert body["export_hash"] == sha256_text(body["jsonl"])
    assert build_event_export(tmp_path)["export_hash"] == body["export_hash"]
