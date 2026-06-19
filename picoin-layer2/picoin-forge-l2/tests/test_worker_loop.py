from __future__ import annotations

from fastapi.testclient import TestClient

from picoin_forge_l2.common.models import AIInferenceRequest, ComputeChallenge
from picoin_forge_l2.coordinator import main as coordinator_main
from picoin_forge_l2.worker.ai_model import AIInferenceOutput
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
            return [ComputeChallenge.model_validate(row) for row in rows]

        def request_challenge(self, worker_id, challenge_type="cpu", difficulty=1):
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


def test_worker_loop_claims_and_submits_ai_request(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path / "coordinator"))
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_NAME", "llama-3.1-70b")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PARAMETERS_B", "70")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS", "131072")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CAPABILITIES", "llm,chat,reasoning")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_ENDPOINT", "http://127.0.0.1:11434")
    client = TestClient(coordinator_main.api)
    registration = register_worker("PIAIWORKERLOOP123", tmp_path / "worker")
    registry = coordinator_main.registry()
    state = registry.register(registration)
    state.ai_model_score = 240.0
    registry.put(state)
    created = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PIREQUESTER",
            "stake_snapshot_pi": 5,
            "prompt": "Explain the Picoin AI layer.",
            "required_capabilities": ["chat"],
            "max_tokens": 64,
        },
    ).json()

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
            return [ComputeChallenge.model_validate(row) for row in rows]

        def request_challenge(self, worker_id, challenge_type="cpu", difficulty=1):
            row = client.post(
                "/challenges",
                json={"worker_id": worker_id, "challenge_type": challenge_type, "difficulty": difficulty},
            ).json()
            return ComputeChallenge.model_validate(row)

        def submit_challenge_result(self, challenge_id, result):
            return client.post(f"/challenges/{challenge_id}/submit", json=result.model_dump(mode="json")).json()

        def claim_ai_request(self, worker_id):
            row = client.post("/ai/requests/claim", json={"worker_id": worker_id}).json()["request"]
            return AIInferenceRequest.model_validate(row) if row else None

        def submit_ai_request_result(self, request_id, result):
            return client.post(f"/ai/requests/{request_id}/submit", json=result.model_dump(mode="json")).json()

    def fake_run_ai_inference(prompt, *, max_tokens=256, profile=None):
        assert prompt == "Explain the Picoin AI layer."
        assert max_tokens == 64
        assert profile is not None
        assert profile.model_name == "llama-3.1-70b"
        return AIInferenceOutput(
            accepted=True,
            output="Picoin Forge routes stake-gated access to verified AI model capacity.",
            backend="test-ai-model",
            output_hash="fake-output-hash",
        )

    monkeypatch.setattr("picoin_forge_l2.worker.loop.CoordinatorClient", LocalClient)
    monkeypatch.setattr("picoin_forge_l2.worker.loop.run_ai_inference", fake_run_ai_inference)

    result = run_worker_loop(
        state_dir=tmp_path / "worker",
        coordinator_url="http://testserver",
        iterations=1,
        interval_seconds=1,
    )

    assert result["last_result"]["ai_requests_handled"] == [
        {
            "request_id": created["request_id"],
            "backend": "test-ai-model",
            "output_hash": "fake-output-hash",
        }
    ]
    stored = coordinator_main.ai_requests().get(created["request_id"])
    assert stored.status.value == "verified"
    assert stored.receipt_hash
    assert stored.no_per_task_payment is True
