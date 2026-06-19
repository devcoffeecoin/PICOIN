from __future__ import annotations

import json

import pytest

from picoin_forge_l2.coordinator import main as coordinator_main
from picoin_forge_l2.worker.registration import register_worker

typer_testing = pytest.importorskip("typer.testing")
CliRunner = typer_testing.CliRunner

pytestmark = pytest.mark.skipif(coordinator_main.app is None, reason="Typer CLI is not installed")


def test_coordinator_ai_cli_create_status_and_export(tmp_path):
    runner = CliRunner()

    created_result = runner.invoke(
        coordinator_main.app,
        [
            "ai",
            "create-request",
            "PIREQUESTER",
            "Explain Picoin Forge.",
            "5",
            "--state-dir",
            str(tmp_path),
            "--capabilities",
            "chat,reasoning",
        ],
    )
    assert created_result.exit_code == 0
    created = json.loads(created_result.output)
    assert created["status"] == "queued"
    assert created["no_per_task_payment"] is True

    status_result = runner.invoke(
        coordinator_main.app,
        ["ai", "status", created["request_id"], "--state-dir", str(tmp_path)],
    )
    assert status_result.exit_code == 0
    status = json.loads(status_result.output)
    assert status["request_id"] == created["request_id"]
    assert status["status"] == "queued"
    assert status["result_ready"] is False

    export_result = runner.invoke(
        coordinator_main.app,
        ["ai", "export-request", created["request_id"], "--state-dir", str(tmp_path)],
    )
    assert export_result.exit_code == 0
    exported = json.loads(export_result.output)
    assert exported["export_hash"]
    assert exported["export"]["prompt_hash"] == created["prompt_hash"]
    assert "prompt" not in exported["export"]


def test_coordinator_ai_cli_capabilities(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_NAME", "llama-3.1-70b")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_PARAMETERS_B", "70")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS", "131072")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_CAPABILITIES", "llm,chat,reasoning")
    monkeypatch.setenv("PICOIN_FORGE_AI_MODEL_ENDPOINT", "http://127.0.0.1:11434")
    registration = register_worker("PIAICLIWORKER", tmp_path / "worker")
    registry = coordinator_main.registry(tmp_path)
    state = registry.register(registration)
    state.ai_model_score = 200.0
    registry.put(state)

    result = runner.invoke(coordinator_main.app, ["ai", "capabilities", "--state-dir", str(tmp_path)])

    assert result.exit_code == 0
    capabilities = json.loads(result.output)
    assert capabilities["ready_worker_count"] == 1
    assert capabilities["providers"] == {"ollama": 1}
    assert capabilities["capabilities"]["reasoning"] == 1
    assert capabilities["models"][0]["model_name"] == "llama-3.1-70b"
