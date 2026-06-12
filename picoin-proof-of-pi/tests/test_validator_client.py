from pathlib import Path
from types import SimpleNamespace

import requests

from app.core.signatures import generate_keypair
from validator import client as validator_client


def _validate_args(*, once: bool = False, loops: int = 2, poll_seconds: float | None = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        server="http://coordinator.example",
        identity=Path("validator.json"),
        node_server="http://127.0.0.1:8000",
        node_timeout=10.0,
        submit_timeout=90.0,
        loops=loops,
        sleep=0.0,
        poll_seconds=poll_seconds,
        heartbeat_interval=30.0,
        workers=1,
        once=once,
    )


def test_request_error_summary_includes_http_response_detail() -> None:
    class Response:
        text = '{"detail":"validator stale"}'

        def json(self):
            return {"detail": "validator stale"}

    exc = requests.HTTPError("403 Client Error: Forbidden")
    exc.response = Response()

    assert validator_client._request_error_summary(exc) == (
        "403 Client Error: Forbidden detail=validator stale"
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


def test_command_validate_polls_job_before_heartbeat(monkeypatch) -> None:
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
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("heartbeat should not block an available job")),
    )
    monkeypatch.setattr(validator_client, "get_job", lambda *args, **kwargs: {"job_id": "job_ok", "task_id": "task_ok"})
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

    assert validator_client.command_validate(_validate_args(once=True, loops=1)) == 0


def test_command_validate_reconciles_configured_peers_after_empty_poll(monkeypatch, capsys) -> None:
    identity = {
        "validator_id": "validator_test",
        "public_key": "ed25519:test",
        "private_key": "private-test",
        "name": "validator-test",
    }
    peers_seen: list[str] = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    monkeypatch.setenv(
        "PICOIN_RECONCILE_PEERS",
        "http://coordinator.example,http://peer-a:8000,http://peer-b:8000=",
    )
    monkeypatch.setenv("PICOIN_VALIDATOR_RECONCILE_INTERVAL_SECONDS", "0")
    monkeypatch.setattr(validator_client, "load_or_register_identity", lambda server_url, path: identity)
    monkeypatch.setattr(validator_client, "send_validator_heartbeat", lambda *args, **kwargs: {"eligible": True})

    def fake_post(url, params=None, timeout=0, **kwargs):
        assert url == "http://coordinator.example/node/reconcile"
        peers_seen.append(params["peer_address"])
        return Response({"validation_jobs_imported": 1, "validation_votes_imported": 0, "validator_heartbeats_imported": 0})

    job_polls = {"count": 0}

    def get_job(server_url, loaded_identity):
        job_polls["count"] += 1
        if job_polls["count"] == 1:
            assert peers_seen == []
            return None
        assert peers_seen == ["http://peer-a:8000", "http://peer-b:8000"]
        return {"job_id": "job_ok", "task_id": "task_ok"}

    monkeypatch.setattr(validator_client.requests, "post", fake_post)
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

    assert validator_client.command_validate(_validate_args(loops=2)) == 0
    captured = capsys.readouterr()
    assert "Validator reconcile imported jobs=2 votes=0 heartbeats=0 peers=2" in captured.out


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


def test_command_validate_backs_off_failed_heartbeat_while_polling(monkeypatch) -> None:
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
        if calls["heartbeats"] == 1:
            raise requests.ReadTimeout("node timed out")
        return {"eligible": True}

    def get_job(*args, **kwargs):
        calls["jobs"] += 1
        return None

    monkeypatch.setattr(validator_client, "send_validator_heartbeat", heartbeat)
    monkeypatch.setattr(validator_client, "get_job", get_job)

    assert validator_client.command_validate(_validate_args(loops=2)) == 0
    assert calls == {"heartbeats": 1, "jobs": 2}


def test_command_validate_uses_sleep_when_poll_seconds_unset(monkeypatch) -> None:
    identity = {
        "validator_id": "validator_test",
        "public_key": "ed25519:test",
        "private_key": "private-test",
        "name": "validator-test",
    }
    sleeps: list[float] = []

    monkeypatch.setattr(validator_client, "load_or_register_identity", lambda server_url, path: identity)
    monkeypatch.setattr(validator_client, "send_validator_heartbeat", lambda *args, **kwargs: {"eligible": True})
    monkeypatch.setattr(validator_client, "get_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(validator_client.time, "sleep", sleeps.append)

    args = _validate_args(loops=2, poll_seconds=None)
    args.sleep = 0.2

    assert validator_client.command_validate(args) == 0
    assert sleeps == [0.2]


def test_send_validator_heartbeat_prefers_liveness_status(monkeypatch) -> None:
    keys = generate_keypair()
    identity = {
        "validator_id": "validator_test",
        "public_key": keys["public_key"],
        "private_key": keys["private_key"],
    }
    calls: list[tuple[str, str]] = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=0):
        calls.append(("get", url))
        if url.endswith("/node/liveness"):
            return Response(
                {
                    "node_id": "node-a",
                    "peer_address": "http://node-a:8000",
                    "effective_latest_block_height": 10,
                    "latest_block_height": 10,
                    "local_block_height": 10,
                    "effective_latest_block_hash": "a" * 64,
                    "latest_block_hash": "a" * 64,
                    "pending_replay_blocks": 0,
                    "protocol_version": "1.0",
                }
            )
        if url.endswith("/node/sync-status"):
            raise requests.ReadTimeout("sync-status timed out")
        raise AssertionError(url)

    def fake_post(url, json=None, timeout=0):
        calls.append(("post", url))
        assert json["effective_height"] == 10
        assert json["latest_block_hash"] == "a" * 64
        return Response({"eligible": True})

    monkeypatch.setattr(validator_client.requests, "get", fake_get)
    monkeypatch.setattr(validator_client.requests, "post", fake_post)

    result = validator_client.send_validator_heartbeat(
        "http://coordinator.example",
        identity,
        node_server_url="http://node-a:8000",
        timeout=1.0,
    )

    assert result == {"eligible": True}
    assert ("get", "http://node-a:8000/node/liveness") in calls
    assert ("get", "http://coordinator.example/node/liveness") not in calls
    assert ("get", "http://node-a:8000/node/sync-status") not in calls
    assert ("post", "http://coordinator.example/validators/heartbeat") in calls


def test_send_validator_heartbeat_falls_back_to_sync_status(monkeypatch) -> None:
    keys = generate_keypair()
    identity = {
        "validator_id": "validator_test",
        "public_key": keys["public_key"],
        "private_key": keys["private_key"],
    }
    calls: list[tuple[str, str]] = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=0):
        calls.append(("get", url))
        if url.endswith("/node/liveness"):
            response = Response({"detail": "not found"})

            def raise_not_found():
                raise requests.HTTPError("404 not found")

            response.raise_for_status = raise_not_found
            return response
        if url.endswith("/node/sync-status"):
            return Response(
                {
                    "node_id": "node-a",
                    "peer_address": "http://node-a:8000",
                    "effective_latest_block_height": 11,
                    "latest_block_height": 11,
                    "local_block_height": 11,
                    "effective_latest_block_hash": "b" * 64,
                    "latest_block_hash": "b" * 64,
                    "pending_replay_blocks": 0,
                    "protocol_version": "1.0",
                }
            )
        raise AssertionError(url)

    def fake_post(url, json=None, timeout=0):
        calls.append(("post", url))
        assert json["effective_height"] == 11
        assert json["latest_block_hash"] == "b" * 64
        return Response({"eligible": True})

    monkeypatch.setattr(validator_client.requests, "get", fake_get)
    monkeypatch.setattr(validator_client.requests, "post", fake_post)

    result = validator_client.send_validator_heartbeat(
        "http://coordinator.example",
        identity,
        node_server_url="http://node-a:8000",
        timeout=1.0,
    )

    assert result == {"eligible": True}
    assert ("get", "http://node-a:8000/node/liveness") in calls
    assert ("get", "http://node-a:8000/node/sync-status") in calls
    assert ("get", "http://coordinator.example/node/liveness") not in calls
    assert ("get", "http://coordinator.example/node/sync-status") not in calls
    assert ("post", "http://coordinator.example/validators/heartbeat") in calls
