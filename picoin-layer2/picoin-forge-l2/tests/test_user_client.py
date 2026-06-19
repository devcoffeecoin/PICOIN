from __future__ import annotations

import json

import pytest

from picoin_forge_l2.client.http import ForgeHTTPClient
from picoin_forge_l2.client import main as client_main


def test_http_client_builds_ai_create_request(monkeypatch):
    captured = {}

    def fake_post(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"request_id": "ai_req_test", "status": "queued"}

    monkeypatch.setattr(ForgeHTTPClient, "post", fake_post)

    result = ForgeHTTPClient("http://coordinator:9380").ai_create_request(
        requester_wallet="PIREQUESTER",
        prompt="Explain Picoin Forge.",
        stake_snapshot_pi=25,
        required_capabilities=["chat", "reasoning"],
        preferred_provider="ollama",
        max_tokens=128,
        store_output=False,
    )

    assert result["request_id"] == "ai_req_test"
    assert captured["path"] == "/ai/requests"
    assert captured["payload"]["requester_wallet"] == "PIREQUESTER"
    assert captured["payload"]["required_capabilities"] == ["chat", "reasoning"]
    assert captured["payload"]["preferred_provider"] == "ollama"
    assert captured["payload"]["store_output"] is False


def test_http_client_ai_run_waits_for_result_and_receipt(monkeypatch):
    calls = {"status": 0}

    def fake_create(self, **kwargs):
        return {"request_id": "ai_req_run", "status": "queued", "prompt_hash": "abc"}

    def fake_status(self, request_id):
        calls["status"] += 1
        if calls["status"] == 1:
            return {"request_id": request_id, "status": "assigned", "result_ready": False}
        return {"request_id": request_id, "status": "verified", "result_ready": True, "receipt_ready": True}

    def fake_result(self, request_id):
        return {"request_id": request_id, "output": "Picoin Forge response.", "output_hash": "out_hash"}

    def fake_receipt(self, request_id):
        return {"request_id": request_id, "valid": True, "receipt_hash": "receipt_hash"}

    monkeypatch.setattr(ForgeHTTPClient, "ai_create_request", fake_create)
    monkeypatch.setattr(ForgeHTTPClient, "ai_status", fake_status)
    monkeypatch.setattr(ForgeHTTPClient, "ai_result", fake_result)
    monkeypatch.setattr(ForgeHTTPClient, "ai_receipt", fake_receipt)

    result = ForgeHTTPClient("http://coordinator:9380").ai_run(
        requester_wallet="PIREQUESTER",
        prompt="Explain Picoin Forge.",
        stake_snapshot_pi=25,
        poll_interval_seconds=0.1,
        wait_timeout_seconds=1,
    )

    assert calls["status"] == 2
    assert result["request"]["request_id"] == "ai_req_run"
    assert result["status"]["status"] == "verified"
    assert result["result"]["output_hash"] == "out_hash"
    assert result["receipt"]["valid"] is True
    assert result["timed_out"] is False
    assert result["no_per_task_payment"] is True


def test_http_client_ai_wait_times_out(monkeypatch):
    monkeypatch.setattr(
        ForgeHTTPClient,
        "ai_status",
        lambda self, request_id: {"request_id": request_id, "status": "assigned"},
    )

    status = ForgeHTTPClient("http://coordinator:9380").ai_wait(
        "ai_req_slow",
        poll_interval_seconds=0.1,
        wait_timeout_seconds=0,
    )

    assert status["request_id"] == "ai_req_slow"
    assert status["status"] == "assigned"
    assert status["timed_out"] is True


def test_http_client_cli_ai_create(monkeypatch):
    typer_testing = pytest.importorskip("typer.testing")
    if client_main.app is None:
        pytest.skip("Typer CLI is not installed")
    runner = typer_testing.CliRunner()

    def fake_create(self, **kwargs):
        return {
            "request_id": "ai_req_cli",
            "status": "queued",
            "required_capabilities": kwargs["required_capabilities"],
            "no_per_task_payment": True,
        }

    monkeypatch.setattr(ForgeHTTPClient, "ai_create_request", fake_create)

    result = runner.invoke(
        client_main.app,
        [
            "ai",
            "create",
            "PIREQUESTER",
            "Explain Picoin Forge.",
            "25",
            "--capabilities",
            "chat,reasoning",
            "--coordinator-url",
            "http://127.0.0.1:9380",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["request_id"] == "ai_req_cli"
    assert payload["required_capabilities"] == ["chat", "reasoning"]
    assert payload["no_per_task_payment"] is True


def test_http_client_cli_ai_run(monkeypatch):
    typer_testing = pytest.importorskip("typer.testing")
    if client_main.app is None:
        pytest.skip("Typer CLI is not installed")
    runner = typer_testing.CliRunner()

    def fake_run(self, **kwargs):
        return {
            "request": {"request_id": "ai_req_run_cli"},
            "status": {"status": "verified"},
            "result": {"output_hash": "out_hash"},
            "receipt": {"valid": True},
            "no_per_task_payment": True,
        }

    monkeypatch.setattr(ForgeHTTPClient, "ai_run", fake_run)

    result = runner.invoke(
        client_main.app,
        [
            "ai",
            "run",
            "PIREQUESTER",
            "Explain Picoin Forge.",
            "25",
            "--capabilities",
            "chat,reasoning",
            "--wait-timeout-seconds",
            "1",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["request"]["request_id"] == "ai_req_run_cli"
    assert payload["status"]["status"] == "verified"
    assert payload["receipt"]["valid"] is True
