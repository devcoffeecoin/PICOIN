from __future__ import annotations

from fastapi.testclient import TestClient

from picoin_forge_l2.common.models import AIInferenceResult
from picoin_forge_l2.coordinator import main as coordinator_main
from picoin_forge_l2.worker.registration import register_worker


def test_ai_chat_session_syncs_verified_message(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_FORGE_AI_ACCESS_MIN_STAKE_PI", "5")
    registration = _register_ai_worker(tmp_path, monkeypatch)
    registry = coordinator_main.registry()
    state = registry.get(registration.worker_id)
    state.ai_model_score = 200.0
    registry.put(state)
    client = TestClient(coordinator_main.api)

    session_response = client.post(
        "/ai/chat/sessions",
        json={
            "requester_wallet": "PIREQUESTER",
            "stake_snapshot_pi": 25,
            "title": "Picoin Forge chat",
            "required_capabilities": ["chat", "reasoning"],
            "max_tokens": 128,
        },
    )
    assert session_response.status_code == 200
    session = session_response.json()
    assert session["session_id"].startswith("ai_chat_")
    assert session["message_count"] == 0
    assert session["no_per_task_payment"] is True

    message_response = client.post(
        f"/ai/chat/sessions/{session['session_id']}/messages",
        json={"prompt": "Explain the staking-gated AI access model."},
    )
    assert message_response.status_code == 200
    created = message_response.json()
    assert created["session"]["message_count"] == 1
    assert created["message"]["role"] == "user"
    assert created["message"]["request_id"] == created["ai_request"]["request_id"]
    assert "User: Explain the staking-gated AI access model." in created["ai_request"]["prompt"]
    assert created["ai_request"]["no_per_task_payment"] is True

    messages = client.get(f"/ai/chat/sessions/{session['session_id']}/messages").json()
    assert len(messages) == 1
    assert messages[0]["content"] == "Explain the staking-gated AI access model."

    claim = client.post("/ai/requests/claim", json={"worker_id": registration.worker_id}).json()["request"]
    assert claim["request_id"] == created["ai_request"]["request_id"]

    result = AIInferenceResult(
        request_id=created["ai_request"]["request_id"],
        worker_id=registration.worker_id,
        output="Picoin Forge uses staking for access and rewards workers by network contribution.",
    )
    submit_response = client.post(
        f"/ai/requests/{created['ai_request']['request_id']}/submit",
        json=result.model_dump(mode="json"),
    )
    assert submit_response.status_code == 200

    sync_response = client.post(
        f"/ai/chat/sessions/{session['session_id']}/sync",
        params={"request_id": created["ai_request"]["request_id"]},
    )
    assert sync_response.status_code == 200
    synced = sync_response.json()
    assert synced["synced"] is True
    assert synced["request_status"] == "verified"
    assert synced["assistant_message"]["role"] == "assistant"
    assert synced["assistant_message"]["content"] == (
        "Picoin Forge uses staking for access and rewards workers by network contribution."
    )
    assert synced["assistant_message"]["receipt_hash"]
    assert synced["assistant_message"]["no_per_task_payment"] is True

    messages = client.get(f"/ai/chat/sessions/{session['session_id']}/messages").json()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["receipt_hash"] == synced["assistant_message"]["receipt_hash"]


def test_ai_chat_requires_session(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    client = TestClient(coordinator_main.api)

    response = client.post("/ai/chat/sessions/missing/messages", json={"prompt": "hello"})

    assert response.status_code == 404


def _register_ai_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_NAME", "llama-3.1-70b")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PARAMETERS_B", "70")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS", "131072")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CAPABILITIES", "llm,chat,reasoning")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_ENDPOINT", "http://127.0.0.1:11434")
    registration = register_worker("PIAICHATWORKER", tmp_path / "worker")
    coordinator_main.registry().register(registration)
    return registration
