from __future__ import annotations

from fastapi.testclient import TestClient

from picoin_forge_l2.common.models import WorkloadResult
from picoin_forge_l2.coordinator import main as coordinator_main
from picoin_forge_l2.worker.registration import register_worker
from picoin_forge_l2.worker.workloads import solve_workload


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
