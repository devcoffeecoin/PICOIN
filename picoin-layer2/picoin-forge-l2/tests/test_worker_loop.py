from __future__ import annotations

from fastapi.testclient import TestClient

from picoin_forge_l2.coordinator import main as coordinator_main
from picoin_forge_l2.worker.loop import run_worker_loop
from picoin_forge_l2.worker.registration import register_worker


def test_worker_loop_runs_single_iteration_against_coordinator(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path / "coordinator"))
    client = TestClient(coordinator_main.api)
    registration = register_worker("PILOOPWORKER123", tmp_path / "worker")

    class LocalClient:
        def __init__(self, base_url: str, **kwargs):
            self.base_url = base_url

        def register(self, worker_registration):
            return client.post("/workers/register", json=worker_registration.model_dump(mode="json")).json()

        def submit_benchmark(self, benchmark):
            return client.post("/benchmarks", json=benchmark.model_dump(mode="json")).json()

        def heartbeat(self, heartbeat):
            return client.post("/heartbeats", json=heartbeat.model_dump(mode="json")).json()

        def open_challenges(self, worker_id):
            rows = client.get(f"/workers/{worker_id}/challenges?open_only=true").json()
            from picoin_forge_l2.common.models import ComputeChallenge

            return [ComputeChallenge.model_validate(row) for row in rows]

        def request_challenge(self, worker_id, challenge_type="cpu", difficulty=1):
            from picoin_forge_l2.common.models import ComputeChallenge

            row = client.post(
                "/challenges",
                json={"worker_id": worker_id, "challenge_type": challenge_type, "difficulty": difficulty},
            ).json()
            return ComputeChallenge.model_validate(row)

        def submit_challenge_result(self, challenge_id, result):
            return client.post(f"/challenges/{challenge_id}/submit", json=result.model_dump(mode="json")).json()

    monkeypatch.setattr("picoin_forge_l2.worker.loop.CoordinatorClient", LocalClient)

    result = run_worker_loop(
        state_dir=tmp_path / "worker",
        coordinator_url="http://testserver",
        iterations=1,
        interval_seconds=1,
    )

    assert result["iterations"] == 1
    assert result["last_result"]["worker_id"] == registration.worker_id
    assert len(result["last_result"]["challenges_solved"]) == 1
