from __future__ import annotations

from fastapi.testclient import TestClient

from picoin_forge_l2.common.models import WorkloadResult
from picoin_forge_l2.coordinator import main as coordinator_main
from picoin_forge_l2.worker.registration import register_worker
from picoin_forge_l2.worker.workloads import classify_text_payload, solve_workload, summarize_text_payload


def test_workload_queue_verifies_hash_text_task(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    client = TestClient(coordinator_main.api)
    registration = register_worker("PIWORKLOADWORKER", tmp_path / "worker")
    assert client.post("/workers/register", json=registration.model_dump(mode="json")).status_code == 200

    created_response = client.post(
        "/workloads",
        json={"task_type": "hash_text", "payload": {"text": "hello picoin"}},
    )
    assert created_response.status_code == 200
    created = created_response.json()
    assert created["status"] == "queued"

    claim_response = client.post("/workloads/claim", json={"worker_id": registration.worker_id})
    assert claim_response.status_code == 200
    claimed = claim_response.json()["task"]
    assert claimed["task_id"] == created["task_id"]
    assert claimed["status"] == "assigned"

    task = coordinator_main.workloads().get(created["task_id"])
    result = WorkloadResult(
        task_id=task.task_id,
        worker_id=registration.worker_id,
        result_hash=solve_workload(task),
    )
    submit_response = client.post(
        f"/workloads/{task.task_id}/submit",
        json=result.model_dump(mode="json"),
    )
    assert submit_response.status_code == 200
    submitted = submit_response.json()
    assert submitted["accepted"] is True
    assert submitted["task"]["status"] == "verified"


def test_text_classification_payload_is_deterministic():
    result = classify_text_payload(
        {
            "text": "GPU available. GPU worker is ready for inference.",
            "labels": {
                "compute": ["gpu", "inference"],
                "payment": ["invoice", "payment"],
            },
            "default_label": "general",
        }
    )

    assert result["label"] == "compute"
    assert result["scores"]["compute"] == 3
    assert result["scores"]["payment"] == 0


def test_workload_queue_verifies_text_classify_task(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    client = TestClient(coordinator_main.api)
    registration = register_worker("PITEXTCLASSIFIER", tmp_path / "worker")
    assert client.post("/workers/register", json=registration.model_dump(mode="json")).status_code == 200

    payload = {
        "text": "This batch needs OCR and text extraction.",
        "labels": {
            "vision": ["ocr", "image"],
            "audio": ["speech", "transcription"],
        },
        "default_label": "general",
    }
    created_response = client.post("/workloads", json={"task_type": "text_classify", "payload": payload})
    assert created_response.status_code == 200
    created = created_response.json()

    claim_response = client.post("/workloads/claim", json={"worker_id": registration.worker_id})
    assert claim_response.status_code == 200
    assert claim_response.json()["task"]["task_id"] == created["task_id"]

    task = coordinator_main.workloads().get(created["task_id"])
    result = WorkloadResult(
        task_id=task.task_id,
        worker_id=registration.worker_id,
        result_hash=solve_workload(task),
    )
    submit_response = client.post(f"/workloads/{task.task_id}/submit", json=result.model_dump(mode="json"))

    assert submit_response.status_code == 200
    assert submit_response.json()["accepted"] is True
    assert submit_response.json()["task"]["status"] == "verified"


def test_batch_summarize_payload_is_deterministic():
    result = summarize_text_payload(
        {
            "documents": [
                "GPU workers process inference jobs. The compute network rewards verified workers.",
                "Verified workers receive challenges. The network uses audit hashes.",
            ],
            "max_sentences": 2,
        }
    )

    assert result["sentence_count"] == 4
    assert len(result["sentences"]) == 2
    assert "workers" in result["summary"].lower()


def test_workload_queue_verifies_batch_summarize_task(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    client = TestClient(coordinator_main.api)
    registration = register_worker("PIBATCHSUMMARY", tmp_path / "worker")
    assert client.post("/workers/register", json=registration.model_dump(mode="json")).status_code == 200

    payload = {
        "documents": [
            "Picoin Forge measures available compute. Workers prove uptime with heartbeats.",
            "Workers solve random challenges. Epoch settlement rewards verified compute.",
        ],
        "max_sentences": 2,
    }
    created_response = client.post("/workloads", json={"task_type": "batch_summarize", "payload": payload})
    assert created_response.status_code == 200
    created = created_response.json()

    claim_response = client.post("/workloads/claim", json={"worker_id": registration.worker_id})
    assert claim_response.status_code == 200
    assert claim_response.json()["task"]["task_id"] == created["task_id"]

    task = coordinator_main.workloads().get(created["task_id"])
    result = WorkloadResult(task_id=task.task_id, worker_id=registration.worker_id, result_hash=solve_workload(task))
    submit_response = client.post(f"/workloads/{task.task_id}/submit", json=result.model_dump(mode="json"))

    assert submit_response.status_code == 200
    assert submit_response.json()["accepted"] is True
    assert submit_response.json()["task"]["status"] == "verified"
