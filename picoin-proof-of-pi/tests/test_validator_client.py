from pathlib import Path
from types import SimpleNamespace

import requests

from validator import client as validator_client


def _validate_args(*, once: bool = False, loops: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        server="http://coordinator.example",
        identity=Path("validator.json"),
        node_server="http://127.0.0.1:8000",
        node_timeout=10.0,
        submit_timeout=90.0,
        loops=loops,
        sleep=0.0,
        poll_seconds=0.0,
        heartbeat_interval=30.0,
        workers=1,
        once=once,
    )


def test_command_validate_continues_after_transient_job_poll_timeout(monkeypatch, capsys) -> None:
    identity = {
        "validator_id": "validator_test",
        "public_key": "ed25519:test",
        "private_key": "private-test",
        "name": "validator-test",
    }
    jobs_seen = {"count": 0}

    monkeypatch.setattr(validator_client, "load_or_register_identity", lambda server_url, path: identity)
    monkeypatch.setattr(validator_client, "send_validator_heartbeat", lambda *args, **kwargs: {"eligible": True})

    def get_job(server_url, loaded_identity):
        jobs_seen["count"] += 1
        if jobs_seen["count"] == 1:
            raise requests.ReadTimeout("coordinator timed out")
        return {"job_id": "job_ok", "task_id": "task_ok"}

    monkeypatch.setattr(validator_client, "get_job", get_job)
    monkeypatch.setattr(validator_client, "validate_job", lambda job, workers=1: (True, "ok"))
    monkeypatch.setattr(
        validator_client,
        "submit_result",
        lambda server_url, loaded_identity, job, approved, reason, timeout=90.0: {
            "status": "approved",
            "approvals": 1,
            "required_approvals": 3,
        },
    )

    assert validator_client.command_validate(_validate_args()) == 0
    assert jobs_seen["count"] == 2
    captured = capsys.readouterr()
    assert "Validator coordinator temporarily unavailable while polling validation job" in captured.err
    assert "Done. validation_jobs_completed=1" in captured.out


def test_command_validate_reuses_fresh_heartbeat_for_active_polling(monkeypatch) -> None:
    identity = {
        "validator_id": "validator_test",
        "public_key": "ed25519:test",
        "private_key": "private-test",
        "name": "validator-test",
    }
    calls = {"heartbeats": 0, "jobs": 0}

    monkeypatch.setattr(validator_client, "load_or_register_identity", lambda server_url, path: identity)

    def heartbeat(*args, **kwargs):
        calls["heartbeats"] += 1
        return {"eligible": True}

    def get_job(server_url, loaded_identity):
        calls["jobs"] += 1
        if calls["jobs"] < 3:
            return None
        return {"job_id": "job_ok", "task_id": "task_ok"}

    monkeypatch.setattr(validator_client, "send_validator_heartbeat", heartbeat)
    monkeypatch.setattr(validator_client, "get_job", get_job)
    monkeypatch.setattr(validator_client, "validate_job", lambda job, workers=1: (True, "ok"))
    monkeypatch.setattr(
        validator_client,
        "submit_result",
        lambda server_url, loaded_identity, job, approved, reason, timeout=90.0: {
            "status": "approved",
            "approvals": 1,
            "required_approvals": 3,
        },
    )

    assert validator_client.command_validate(_validate_args(loops=3)) == 0
    assert calls == {"heartbeats": 1, "jobs": 3}


def test_command_validate_once_treats_network_timeout_as_idle(monkeypatch) -> None:
    identity = {
        "validator_id": "validator_test",
        "public_key": "ed25519:test",
        "private_key": "private-test",
        "name": "validator-test",
    }

    monkeypatch.setattr(validator_client, "load_or_register_identity", lambda server_url, path: identity)
    monkeypatch.setattr(
        validator_client,
        "send_validator_heartbeat",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.ReadTimeout("node timed out")),
    )
    monkeypatch.setattr(validator_client, "get_job", lambda *args, **kwargs: None)

    assert validator_client.command_validate(_validate_args(once=True, loops=1)) == 0
