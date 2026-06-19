from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from picoin_forge_l2.common.models import AIInferenceResult, utc_now
from picoin_forge_l2.coordinator import main as coordinator_main
from picoin_forge_l2.worker.registration import register_worker


def test_ai_request_requires_stake_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_FORGE_AI_ACCESS_MIN_STAKE_PI", "10")
    client = TestClient(coordinator_main.api)

    response = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PIREQUESTER",
            "stake_snapshot_pi": 1,
            "prompt": "Explain Picoin Forge.",
            "required_capabilities": ["chat"],
        },
    )

    assert response.status_code == 403
    assert "below required AI access stake" in response.json()["detail"]


def test_ai_request_lifecycle_creates_receipt_without_per_task_payment(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_FORGE_AI_ACCESS_MIN_STAKE_PI", "5")
    registration = _register_ai_worker(tmp_path, monkeypatch, capabilities="llm,chat,reasoning")
    registry = coordinator_main.registry()
    state = registry.get(registration.worker_id)
    state.ai_model_score = 200.0
    registry.put(state)
    client = TestClient(coordinator_main.api)

    created_response = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PIREQUESTER",
            "stake_snapshot_pi": 12,
            "prompt": "Return a short explanation of Picoin Forge.",
            "required_capabilities": ["chat"],
            "max_tokens": 64,
        },
    )
    assert created_response.status_code == 200
    created = created_response.json()
    assert created["status"] == "queued"
    assert created["no_per_task_payment"] is True
    queued_status = client.get(f"/ai/requests/{created['request_id']}/status").json()
    assert queued_status["status"] == "queued"
    assert queued_status["result_ready"] is False
    assert queued_status["receipt_ready"] is False
    assert queued_status["queue_policy"] == "stake_priority_then_created_at"

    claim_response = client.post("/ai/requests/claim", json={"worker_id": registration.worker_id})
    assert claim_response.status_code == 200
    claimed = claim_response.json()["request"]
    assert claimed["request_id"] == created["request_id"]
    assert claimed["status"] == "assigned"
    assert claimed["model_profile"]["model_name"] == "llama-3.1-70b"
    assigned_status = client.get(f"/ai/requests/{created['request_id']}/status").json()
    assert assigned_status["status"] == "assigned"
    assert assigned_status["assigned_worker_id"] == registration.worker_id
    assert assigned_status["assignment_attempts"] == 1
    assert assigned_status["lease_expires_at"]

    result = AIInferenceResult(
        request_id=created["request_id"],
        worker_id=registration.worker_id,
        output="Picoin Forge is a verified AI compute capacity layer.",
    )
    submit_response = client.post(
        f"/ai/requests/{created['request_id']}/submit",
        json=result.model_dump(mode="json"),
    )

    assert submit_response.status_code == 200
    submitted = submit_response.json()
    assert submitted["accepted"] is True
    request_item = submitted["request"]
    assert request_item["status"] == "verified"
    assert request_item["receipt_hash"]
    assert request_item["output_hash"]
    assert request_item["no_l1_transaction_created"] is True
    assert request_item["no_per_task_payment"] is True
    verified_status = client.get(f"/ai/requests/{created['request_id']}/status").json()
    assert verified_status["status"] == "verified"
    assert verified_status["result_ready"] is True
    assert verified_status["output_stored"] is True
    assert verified_status["receipt_ready"] is True

    receipt_response = client.get(f"/ai/requests/{created['request_id']}/receipt")
    assert receipt_response.status_code == 200
    receipt = receipt_response.json()
    assert receipt["valid"] is True
    assert receipt["receipt_hash"] == request_item["receipt_hash"]
    assert receipt["payload"]["worker_id"] == registration.worker_id
    assert receipt["payload"]["no_per_task_payment"] is True

    result_response = client.get(f"/ai/requests/{created['request_id']}/result")
    assert result_response.status_code == 200
    result_payload = result_response.json()
    assert result_payload["output"] == "Picoin Forge is a verified AI compute capacity layer."
    assert result_payload["output_stored"] is True
    assert result_payload["output_hash"] == request_item["output_hash"]
    assert result_payload["receipt_hash"] == request_item["receipt_hash"]
    assert result_payload["no_per_task_payment"] is True
    redacted_export_response = client.get(f"/ai/requests/{created['request_id']}/export")
    assert redacted_export_response.status_code == 200
    redacted_export = redacted_export_response.json()
    assert redacted_export["export_hash"]
    assert redacted_export["include_content"] is False
    assert redacted_export["export"]["prompt_hash"] == created["prompt_hash"]
    assert redacted_export["export"]["output_hash"] == request_item["output_hash"]
    assert redacted_export["export"]["receipt_hash"] == request_item["receipt_hash"]
    assert "prompt" not in redacted_export["export"]
    assert "output" not in redacted_export["export"]
    content_export = client.get(f"/ai/requests/{created['request_id']}/export?include_content=true").json()
    assert content_export["include_content"] is True
    assert content_export["export"]["prompt"] == "Return a short explanation of Picoin Forge."
    assert content_export["export"]["output"] == "Picoin Forge is a verified AI compute capacity layer."
    cancel_verified = client.post(f"/ai/requests/{created['request_id']}/cancel")
    assert cancel_verified.status_code == 409

    summary_response = client.get("/ai/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["min_stake_pi"] == 5
    assert summary["ai_workers_ready"] == 1
    assert summary["request_status_counts"]["verified"] == 1
    assert summary["workers"][0]["model_name"] == "llama-3.1-70b"

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 200
    assert "AI Network" in dashboard_response.text
    assert "llama-3.1-70b" in dashboard_response.text
    assert created["request_id"] in dashboard_response.text


def test_ai_request_can_skip_persisting_output_content(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    registration = _register_ai_worker(tmp_path, monkeypatch, capabilities="llm,chat")
    registry = coordinator_main.registry()
    state = registry.get(registration.worker_id)
    state.ai_model_score = 200.0
    registry.put(state)
    client = TestClient(coordinator_main.api)

    created = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PIPRIVATE",
            "stake_snapshot_pi": 5,
            "prompt": "Private prompt.",
            "required_capabilities": ["chat"],
            "store_output": False,
        },
    ).json()
    assert created["store_output"] is False
    assert client.post("/ai/requests/claim", json={"worker_id": registration.worker_id}).status_code == 200
    submit_response = client.post(
        f"/ai/requests/{created['request_id']}/submit",
        json=AIInferenceResult(
            request_id=created["request_id"],
            worker_id=registration.worker_id,
            output="Private model output.",
        ).model_dump(mode="json"),
    ).json()

    assert submit_response["accepted"] is True
    request_item = submit_response["request"]
    assert request_item["output"] is None
    assert request_item["output_hash"]
    status = client.get(f"/ai/requests/{created['request_id']}/status").json()
    assert status["result_ready"] is True
    assert status["output_stored"] is False
    result = client.get(f"/ai/requests/{created['request_id']}/result").json()
    assert result["output"] is None
    assert result["output_stored"] is False
    assert result["output_hash"] == request_item["output_hash"]
    exported = client.get(f"/ai/requests/{created['request_id']}/export?include_content=true").json()
    assert exported["export"]["prompt"] == "Private prompt."
    assert exported["export"]["output"] is None


def test_ai_request_claim_requires_matching_capabilities(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    registration = _register_ai_worker(tmp_path, monkeypatch, capabilities="llm,chat")
    registry = coordinator_main.registry()
    state = registry.get(registration.worker_id)
    state.ai_model_score = 200.0
    registry.put(state)
    client = TestClient(coordinator_main.api)

    assert client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PIREQUESTER",
            "stake_snapshot_pi": 5,
            "prompt": "Transcribe this audio.",
            "required_capabilities": ["transcription"],
        },
    ).status_code == 200

    claim_response = client.post("/ai/requests/claim", json={"worker_id": registration.worker_id})

    assert claim_response.status_code == 200
    assert claim_response.json()["request"] is None


def test_ai_request_claim_prioritizes_higher_stake_without_payment(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    registration = _register_ai_worker(tmp_path, monkeypatch, capabilities="llm,chat")
    registry = coordinator_main.registry()
    state = registry.get(registration.worker_id)
    state.ai_model_score = 200.0
    registry.put(state)
    client = TestClient(coordinator_main.api)

    low_stake = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PILOWSTAKE",
            "stake_snapshot_pi": 5,
            "prompt": "Low stake request.",
            "required_capabilities": ["chat"],
        },
    ).json()
    high_stake = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PIHIGHSTAKE",
            "stake_snapshot_pi": 50,
            "prompt": "High stake request.",
            "required_capabilities": ["chat"],
        },
    ).json()

    claimed = client.post("/ai/requests/claim", json={"worker_id": registration.worker_id}).json()["request"]

    assert claimed["request_id"] == high_stake["request_id"]
    assert claimed["request_id"] != low_stake["request_id"]
    assert claimed["no_per_task_payment"] is True
    assert claimed["no_l1_transaction_created"] is True
    summary = client.get("/ai/summary").json()
    assert summary["queue_policy"] == "stake_priority_then_created_at"


def test_ai_request_cancel_queued_request(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    client = TestClient(coordinator_main.api)
    created = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PICANCEL",
            "stake_snapshot_pi": 5,
            "prompt": "Cancel this before assignment.",
            "required_capabilities": ["chat"],
        },
    ).json()

    canceled = client.post(f"/ai/requests/{created['request_id']}/cancel").json()
    status = client.get(f"/ai/requests/{created['request_id']}/status").json()

    assert canceled["canceled"] is True
    assert canceled["request"]["status"] == "canceled"
    assert canceled["request"]["failure_reason"] == "requester_canceled"
    assert status["status"] == "canceled"
    assert status["no_per_task_payment"] is True


def test_ai_request_cancel_assigned_request_releases_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    worker = _register_ai_worker(tmp_path, monkeypatch, capabilities="llm,chat")
    registry = coordinator_main.registry()
    state = registry.get(worker.worker_id)
    state.ai_model_score = 200.0
    registry.put(state)
    client = TestClient(coordinator_main.api)
    created = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PICANCELASSIGNED",
            "stake_snapshot_pi": 5,
            "prompt": "Cancel this after assignment.",
            "required_capabilities": ["chat"],
        },
    ).json()
    claim = client.post("/ai/requests/claim", json={"worker_id": worker.worker_id}).json()["request"]
    assert claim["status"] == "assigned"

    canceled = client.post(f"/ai/requests/{created['request_id']}/cancel").json()["request"]

    assert canceled["status"] == "canceled"
    assert canceled["assigned_worker_id"] is None
    assert canceled["assigned_at"] is None
    assert canceled["lease_expires_at"] is None
    assert canceled["failure_reason"] == "requester_canceled"
    assert client.post("/ai/requests/claim", json={"worker_id": worker.worker_id}).json()["request"] is None


def test_ai_routing_prefers_best_verified_model_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    small = _register_ai_worker_with(
        tmp_path,
        monkeypatch,
        wallet="PIAISMA11WORKER",
        state_name="small",
        model_name="llama-3.1-8b",
        parameter_count_b=8,
        context_tokens=8192,
        capabilities="llm,chat",
    )
    large = _register_ai_worker_with(
        tmp_path,
        monkeypatch,
        wallet="PIAILARGEWORKER",
        state_name="large",
        model_name="llama-3.1-70b",
        parameter_count_b=70,
        context_tokens=131072,
        capabilities="llm,chat,reasoning",
    )
    registry = coordinator_main.registry()
    small_state = registry.get(small.worker_id)
    small_state.ai_model_score = 120.0
    registry.put(small_state)
    large_state = registry.get(large.worker_id)
    large_state.ai_model_score = 300.0
    registry.put(large_state)
    client = TestClient(coordinator_main.api)

    created = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PIREQUESTER",
            "stake_snapshot_pi": 5,
            "prompt": "Answer with reasoning.",
            "required_capabilities": ["chat", "reasoning"],
            "min_parameter_count_b": 30,
            "min_context_tokens": 32000,
        },
    ).json()
    routing = client.get(f"/ai/requests/{created['request_id']}/routing").json()

    assert routing["selected_worker_id"] == large.worker_id
    assert routing["candidate_count"] == 1
    assert routing["candidates"][0]["model_name"] == "llama-3.1-70b"
    assert client.post("/ai/requests/claim", json={"worker_id": small.worker_id}).json()["request"] is None
    assert client.post("/ai/requests/claim", json={"worker_id": large.worker_id}).json()["request"]["request_id"] == created["request_id"]


def test_ai_routing_distributes_away_from_loaded_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    first = _register_ai_worker_with(
        tmp_path,
        monkeypatch,
        wallet="PIAIFIRSTWORKER",
        state_name="first",
        model_name="llama-3.1-70b-a",
        parameter_count_b=70,
        context_tokens=131072,
        capabilities="llm,chat",
    )
    second = _register_ai_worker_with(
        tmp_path,
        monkeypatch,
        wallet="PIAISECONDWORKER",
        state_name="second",
        model_name="llama-3.1-70b-b",
        parameter_count_b=70,
        context_tokens=131072,
        capabilities="llm,chat",
    )
    registry = coordinator_main.registry()
    first_state = registry.get(first.worker_id)
    first_state.ai_model_score = 220.0
    registry.put(first_state)
    second_state = registry.get(second.worker_id)
    second_state.ai_model_score = 210.0
    registry.put(second_state)
    client = TestClient(coordinator_main.api)

    first_request = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PIREQUESTER",
            "stake_snapshot_pi": 5,
            "prompt": "First request.",
            "required_capabilities": ["chat"],
        },
    ).json()
    assert client.post("/ai/requests/claim", json={"worker_id": first.worker_id}).json()["request"]["request_id"] == first_request["request_id"]

    second_request = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PIREQUESTER",
            "stake_snapshot_pi": 5,
            "prompt": "Second request.",
            "required_capabilities": ["chat"],
        },
    ).json()
    routing = client.get(f"/ai/requests/{second_request['request_id']}/routing").json()

    assert routing["selected_worker_id"] == second.worker_id
    assert routing["candidates"][0]["active_requests"] == 0
    assert routing["candidates"][1]["worker_id"] == first.worker_id
    assert routing["candidates"][1]["active_requests"] == 1


def test_ai_capabilities_summarize_ready_model_workers(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    small = _register_ai_worker_with(
        tmp_path,
        monkeypatch,
        wallet="PIAICAPSMALL",
        state_name="cap-small",
        model_name="llama-3.1-8b",
        parameter_count_b=8,
        context_tokens=8192,
        capabilities="llm,chat",
    )
    large = _register_ai_worker_with(
        tmp_path,
        monkeypatch,
        wallet="PIAICAPLARGE",
        state_name="cap-large",
        model_name="llama-3.1-70b",
        parameter_count_b=70,
        context_tokens=131072,
        capabilities="llm,chat,reasoning,tool-use",
    )
    registry = coordinator_main.registry()
    small_state = registry.get(small.worker_id)
    small_state.ai_model_score = 100.0
    registry.put(small_state)
    large_state = registry.get(large.worker_id)
    large_state.ai_model_score = 250.0
    registry.put(large_state)
    client = TestClient(coordinator_main.api)

    capabilities = client.get("/ai/capabilities").json()

    assert capabilities["ready_worker_count"] == 2
    assert capabilities["providers"] == {"ollama": 2}
    assert capabilities["capabilities"]["chat"] == 2
    assert capabilities["capabilities"]["reasoning"] == 1
    assert capabilities["max_parameter_count_b"] == 70
    assert capabilities["max_context_tokens"] == 131072
    assert capabilities["models"][0]["model_name"] == "llama-3.1-70b"
    assert capabilities["models"][1]["model_name"] == "llama-3.1-8b"
    assert capabilities["no_per_task_payment"] is True


def test_ai_request_expired_lease_requeues_for_another_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    first = _register_ai_worker_with(
        tmp_path,
        monkeypatch,
        wallet="PIAILEASEFIRST",
        state_name="lease-first",
        model_name="llama-3.1-70b-a",
        parameter_count_b=70,
        context_tokens=131072,
        capabilities="llm,chat",
    )
    second = _register_ai_worker_with(
        tmp_path,
        monkeypatch,
        wallet="PIAILEASESECOND",
        state_name="lease-second",
        model_name="llama-3.1-70b-b",
        parameter_count_b=70,
        context_tokens=131072,
        capabilities="llm,chat",
    )
    registry = coordinator_main.registry()
    first_state = registry.get(first.worker_id)
    first_state.ai_model_score = 220.0
    registry.put(first_state)
    second_state = registry.get(second.worker_id)
    second_state.ai_model_score = 210.0
    registry.put(second_state)
    client = TestClient(coordinator_main.api)

    created = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PIREQUESTER",
            "stake_snapshot_pi": 5,
            "prompt": "Request that should fail over.",
            "required_capabilities": ["chat"],
        },
    ).json()
    claim = client.post("/ai/requests/claim", json={"worker_id": first.worker_id}).json()["request"]
    assert claim["status"] == "assigned"
    assert claim["assignment_attempts"] == 1
    assert claim["lease_expires_at"]

    _expire_request(created["request_id"])
    expired = client.post("/ai/requests/expire").json()

    assert expired["expired"] == 1
    requeued = expired["requests"][0]
    assert requeued["status"] == "queued"
    assert requeued["assigned_worker_id"] is None
    assert requeued["assignment_attempts"] == 1
    assert requeued["assignment_history"] == [first.worker_id]
    assert requeued["failure_reason"] == "assignment_lease_expired"

    next_claim = client.post("/ai/requests/claim", json={"worker_id": second.worker_id}).json()["request"]
    assert next_claim["request_id"] == created["request_id"]
    assert next_claim["assigned_worker_id"] == second.worker_id
    assert next_claim["assignment_attempts"] == 2
    assert next_claim["assignment_history"] == [first.worker_id, second.worker_id]


def test_ai_request_expired_lease_fails_after_max_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_FORGE_AI_REQUEST_MAX_ASSIGNMENTS", "1")
    worker = _register_ai_worker(tmp_path, monkeypatch, capabilities="llm,chat")
    registry = coordinator_main.registry()
    state = registry.get(worker.worker_id)
    state.ai_model_score = 200.0
    registry.put(state)
    client = TestClient(coordinator_main.api)

    created = client.post(
        "/ai/requests",
        json={
            "requester_wallet": "PIREQUESTER",
            "stake_snapshot_pi": 5,
            "prompt": "Request that should fail permanently.",
            "required_capabilities": ["chat"],
        },
    ).json()
    claim = client.post("/ai/requests/claim", json={"worker_id": worker.worker_id}).json()["request"]
    assert claim["assignment_attempts"] == 1

    _expire_request(created["request_id"])
    expired = client.post("/ai/requests/expire").json()

    assert expired["expired"] == 1
    failed = expired["requests"][0]
    assert failed["status"] == "failed"
    assert failed["assigned_worker_id"] == worker.worker_id
    assert failed["assignment_attempts"] == 1
    assert failed["failure_reason"] == "assignment_lease_expired_max_attempts"
    status = client.get(f"/ai/requests/{created['request_id']}/status").json()
    assert status["status"] == "failed"
    assert status["result_ready"] is False
    assert status["receipt_ready"] is False
    assert status["failure_reason"] == "assignment_lease_expired_max_attempts"


def _expire_request(request_id: str) -> None:
    queue = coordinator_main.ai_requests()
    item = queue.get(request_id)
    item.lease_expires_at = utc_now() - timedelta(seconds=1)
    queue.put(item)


def _register_ai_worker(tmp_path, monkeypatch, *, capabilities: str):
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_NAME", "llama-3.1-70b")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PARAMETERS_B", "70")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS", "131072")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CAPABILITIES", capabilities)
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_ENDPOINT", "http://127.0.0.1:11434")
    registration = register_worker("PIAIACCESSWORKER", tmp_path / "worker")
    coordinator_main.registry().register(registration)
    return registration


def _register_ai_worker_with(
    tmp_path,
    monkeypatch,
    *,
    wallet: str,
    state_name: str,
    model_name: str,
    parameter_count_b: float,
    context_tokens: int,
    capabilities: str,
):
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_NAME", model_name)
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PARAMETERS_B", str(parameter_count_b))
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS", str(context_tokens))
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CAPABILITIES", capabilities)
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_ENDPOINT", "http://127.0.0.1:11434")
    registration = register_worker(wallet, tmp_path / state_name)
    coordinator_main.registry().register(registration)
    return registration
