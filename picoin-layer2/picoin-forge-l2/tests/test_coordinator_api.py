from __future__ import annotations

from fastapi.testclient import TestClient

from picoin_forge_l2.coordinator import main as coordinator_main
from picoin_forge_l2.worker.benchmark import run_benchmark
from picoin_forge_l2.worker.challenges import solve_challenge
from picoin_forge_l2.worker.heartbeat import build_heartbeat
from picoin_forge_l2.worker.registration import register_worker


def test_coordinator_api_challenge_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    client = TestClient(coordinator_main.api)
    registration = register_worker("PIAPIFLOW123", tmp_path / "worker")

    register_response = client.post("/workers/register", json=registration.model_dump(mode="json"))
    assert register_response.status_code == 200

    benchmark = run_benchmark(registration.worker_id, scale=1)
    assert client.post("/benchmarks", json=benchmark.model_dump(mode="json")).status_code == 200
    assert client.post("/heartbeats", json=build_heartbeat(registration.worker_id).model_dump(mode="json")).status_code == 200

    challenge_response = client.post(
        "/challenges",
        json={"worker_id": registration.worker_id, "challenge_type": "cpu", "difficulty": 1},
    )
    assert challenge_response.status_code == 200
    challenge = challenge_response.json()

    open_response = client.get(f"/workers/{registration.worker_id}/challenges?open_only=true")
    assert open_response.status_code == 200
    assert len(open_response.json()) == 1

    result = solve_challenge(coordinator_main.challenges().get(challenge["challenge_id"]))
    submit_response = client.post(
        f"/challenges/{challenge['challenge_id']}/submit",
        json=result.model_dump(mode="json"),
    )
    assert submit_response.status_code == 200
    assert submit_response.json()["accepted"] is True

    settlement_response = client.post("/epochs/close?epoch_reward=100")
    assert settlement_response.status_code == 200
    settlement = settlement_response.json()
    assert settlement["epoch_id"] == 1
    assert settlement["workers"][0]["reward_pi"] == 100.0

    events_response = client.get("/events")
    assert events_response.status_code == 200
    assert any(row["event_type"] == "epoch.closed" for row in events_response.json())

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 200
    assert "Picoin Forge L2 Coordinator" in dashboard_response.text
