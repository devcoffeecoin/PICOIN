from datetime import datetime, timedelta, timezone

import pytest

from app.core.signatures import generate_keypair, sign_payload
from app.db.database import get_connection, init_db
from app.services import mining as mining_service
from app.services.mining import (
    MiningError,
    cleanup_expired_tasks,
    get_validation_job,
    get_validation_jobs_health,
    get_validators,
    list_validator_heartbeat_inventory,
    record_validator_heartbeat,
    receive_validator_heartbeat_gossip,
    refresh_participant_liveness,
    register_miner,
    register_validator,
)


def _use_db(tmp_path, monkeypatch, name: str):
    db_path = tmp_path / name
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "_PARTICIPANT_LIVENESS_LAST_RUN_MONOTONIC", 0.0)
    monkeypatch.setattr(mining_service, "_EXPIRED_TASK_CLEANUP_LAST_RUN_MONOTONIC", 0.0)
    with mining_service._STATUS_ENDPOINT_CACHE_LOCK:
        mining_service._STATUS_ENDPOINT_CACHE.clear()
    init_db(db_path)
    return db_path


def _signed_validator_heartbeat(keys: dict[str, str], validator_id: str, **overrides):
    payload = {
        "validator_id": validator_id,
        "node_id": "node-one",
        "public_key": keys["public_key"],
        "address": "http://127.0.0.1:8000",
        "local_height": 100,
        "effective_height": 100,
        "latest_block_hash": "a" * 64,
        "pending_replay_blocks": 0,
        "sync_lag": 0,
        "version": "0.18",
    }
    payload.update(overrides)
    payload["signature"] = sign_payload(keys["private_key"], payload)
    return payload


def test_validator_liveness_transitions_online_stale_offline(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "liveness.sqlite3")
    keys = generate_keypair()
    heartbeat = _signed_validator_heartbeat(keys, "validator_live")

    validator = record_validator_heartbeat(heartbeat)
    assert validator["online_status"] == "online"

    stale_time = datetime.now(timezone.utc) + timedelta(seconds=121)
    refresh_participant_liveness(stale_time, force=True)
    with get_connection() as connection:
        row = connection.execute("SELECT online_status FROM validators WHERE validator_id = 'validator_live'").fetchone()
    assert row["online_status"] == "stale"

    offline_time = datetime.now(timezone.utc) + timedelta(seconds=301)
    refresh_participant_liveness(offline_time, force=True)
    with get_connection() as connection:
        row = connection.execute("SELECT online_status FROM validators WHERE validator_id = 'validator_live'").fetchone()
    assert row["online_status"] == "offline"


def test_offline_validator_is_excluded_from_quorum_eligibility(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "eligibility.sqlite3")
    keys = generate_keypair()
    validator = register_validator("eligible-now", keys["public_key"])
    record_validator_heartbeat(_signed_validator_heartbeat(keys, validator["validator_id"]))
    assert validator["validator_id"] in {item["validator_id"] for item in get_validators(eligible_only=True)}

    with get_connection() as connection:
        connection.execute(
            "UPDATE validators SET last_heartbeat_at = ? WHERE validator_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat(), validator["validator_id"]),
        )
    refresh_participant_liveness(force=True)

    assert validator["validator_id"] not in {item["validator_id"] for item in get_validators(eligible_only=True)}


def test_validator_without_active_node_heartbeat_cannot_get_job(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "validator-node-required.sqlite3")
    keys = generate_keypair()
    validator = register_validator("no-node-validator", keys["public_key"])

    with pytest.raises(MiningError) as exc:
        get_validation_job(validator["validator_id"])

    assert exc.value.status_code == 403
    assert "node heartbeat" in exc.value.detail


def test_invalid_heartbeat_signature_does_not_write_validator(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "bad-heartbeat.sqlite3")
    keys = generate_keypair()
    payload = _signed_validator_heartbeat(keys, "validator_bad_signature")
    payload["signature"] = "invalid"

    with pytest.raises(MiningError) as exc:
        record_validator_heartbeat(payload)

    assert exc.value.status_code == 401
    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM validators WHERE validator_id = 'validator_bad_signature'"
        ).fetchone()
    assert row is None


def test_validator_heartbeat_inventory_can_seed_another_node(tmp_path, monkeypatch) -> None:
    db_a = _use_db(tmp_path, monkeypatch, "heartbeat-node-a.sqlite3")
    keys = generate_keypair()
    payload = _signed_validator_heartbeat(keys, "validator_gossip_a", heartbeat_at=datetime.now(timezone.utc).isoformat())

    local = record_validator_heartbeat(payload)
    inventory = list_validator_heartbeat_inventory()

    assert local["heartbeat_inserted"] is True
    assert inventory["count"] == 1
    assert inventory["heartbeats"][0]["heartbeat_id"] == local["heartbeat_id"]

    db_b = tmp_path / "heartbeat-node-b.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_b)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_b)
    init_db(db_b)

    received = receive_validator_heartbeat_gossip(inventory["heartbeats"][0], source_peer="http://node-a:8000")
    duplicate = receive_validator_heartbeat_gossip(inventory["heartbeats"][0], source_peer="http://node-a:8000")

    assert db_a.exists()
    assert received["status"] == "accepted"
    assert duplicate["status"] == "duplicate"
    assert received["validator"]["validator_id"] == "validator_gossip_a"
    assert received["validator"]["online_status"] == "online"
    with get_connection() as connection:
        row = connection.execute(
            "SELECT source_peer FROM validator_heartbeats WHERE heartbeat_id = ?",
            (received["heartbeat_id"],),
        ).fetchone()
    assert row["source_peer"] == "http://node-a:8000"


def test_invalid_gossiped_validator_heartbeat_is_rejected(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "bad-gossip-heartbeat.sqlite3")
    keys = generate_keypair()
    payload = _signed_validator_heartbeat(keys, "validator_bad_gossip")
    payload["signature"] = "invalid"

    with pytest.raises(MiningError) as exc:
        receive_validator_heartbeat_gossip({"heartbeat": payload, "observed_at": datetime.now(timezone.utc).isoformat()})

    assert exc.value.status_code == 401
    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM validator_heartbeats WHERE validator_id = 'validator_bad_gossip'"
        ).fetchone()
    assert row is None


def test_stale_gossiped_validator_heartbeat_is_not_eligible(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "stale-gossip-heartbeat.sqlite3")
    keys = generate_keypair()
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    payload = _signed_validator_heartbeat(keys, "validator_stale_gossip", heartbeat_at=old_time)

    received = receive_validator_heartbeat_gossip(
        {"heartbeat": payload, "observed_at": old_time},
        source_peer="http://node-a:8000",
    )

    assert received["status"] == "accepted"
    assert received["validator"]["online_status"] == "offline"
    assert "validator_stale_gossip" not in {item["validator_id"] for item in get_validators(eligible_only=True)}


def test_duplicate_validator_public_key_is_disabled(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "duplicate-validator.sqlite3")
    keys = generate_keypair()
    record_validator_heartbeat(_signed_validator_heartbeat(keys, "validator_original"))

    duplicate = record_validator_heartbeat(_signed_validator_heartbeat(keys, "validator_duplicate"))

    assert duplicate["enabled"] is False
    assert duplicate["online_status"] == "duplicated_identity"
    assert duplicate["reason_if_not_eligible"] == "duplicate public key identity detected"


def test_validation_job_reassigned_after_assignment_timeout(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "reassign.sqlite3")
    miner = register_miner("job-miner", generate_keypair()["public_key"])
    first_keys = generate_keypair()
    second_keys = generate_keypair()
    first = register_validator("validator-one", first_keys["public_key"])
    second = register_validator("validator-two", second_keys["public_key"])
    record_validator_heartbeat(_signed_validator_heartbeat(first_keys, first["validator_id"], node_id="node-one"))
    record_validator_heartbeat(_signed_validator_heartbeat(second_keys, second["validator_id"], node_id="node-two", address="http://127.0.0.2:8000"))

    old_assigned_at = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at
            )
            VALUES ('task_reassign', ?, 1000, 1063, 'bbp_hex_v1', 'revealed', ?, ?)
            """,
            (miner["miner_id"], protocol_params_id, now),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, assigned_validator_id, assigned_at, created_at
            )
            VALUES ('job_reassign', 'task_reassign', ?, ?, ?, ?, '[]', 'pending', ?, ?, ?)
            """,
            (miner["miner_id"], "a" * 64, "b" * 64, "c" * 64, first["validator_id"], old_assigned_at, now),
        )

    job = get_validation_job(second["validator_id"])

    assert job is not None
    assert job["job_id"] == "job_reassign"
    assert job["assigned_validator_id"] == second["validator_id"]
    with get_connection() as connection:
        row = connection.execute("SELECT assignment_failures FROM validation_jobs WHERE job_id = 'job_reassign'").fetchone()
    assert row["assignment_failures"] == 1


def test_validation_job_is_visible_to_parallel_eligible_validators(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "parallel-validation.sqlite3")
    miner = register_miner("parallel-miner", generate_keypair()["public_key"])
    first_keys = generate_keypair()
    second_keys = generate_keypair()
    first = register_validator("parallel-validator-one", first_keys["public_key"])
    second = register_validator("parallel-validator-two", second_keys["public_key"])
    record_validator_heartbeat(_signed_validator_heartbeat(first_keys, first["validator_id"], node_id="node-one"))
    record_validator_heartbeat(_signed_validator_heartbeat(second_keys, second["validator_id"], node_id="node-two", address="http://127.0.0.2:8000"))

    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at
            )
            VALUES ('task_parallel', ?, 1000, 1063, 'bbp_hex_v1', 'revealed', ?, ?)
            """,
            (miner["miner_id"], protocol_params_id, now),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, assigned_validator_id, assigned_at, created_at
            )
            VALUES ('job_parallel', 'task_parallel', ?, ?, ?, ?, '[]', 'pending', ?, ?, ?)
            """,
            (miner["miner_id"], "a" * 64, "b" * 64, "c" * 64, first["validator_id"], now, now),
        )

    job = get_validation_job(second["validator_id"])

    assert job is not None
    assert job["job_id"] == "job_parallel"
    assert job["assigned_validator_id"] == second["validator_id"]
    with get_connection() as connection:
        row = connection.execute("SELECT assignment_failures FROM validation_jobs WHERE job_id = 'job_parallel'").fetchone()
    assert row["assignment_failures"] == 0


def test_validation_job_visibility_promotes_expired_task_to_revealed(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "validation-job-promotes-expired-task.sqlite3")
    miner = register_miner("promote-expired-task-miner", generate_keypair()["public_key"])
    validator_keys = generate_keypair()
    validator = register_validator("promote-expired-task-validator", validator_keys["public_key"])
    record_validator_heartbeat(_signed_validator_heartbeat(validator_keys, validator["validator_id"], node_id="node-one"))

    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, expires_at
            )
            VALUES ('task_pending_job_expired_local', ?, 1000, 1063, 'bbp_hex_v1', 'expired', ?, ?, ?)
            """,
            (miner["miner_id"], protocol_params_id, now, "2026-06-01T00:10:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at, job_created_at
            )
            VALUES ('job_pending_with_expired_task', 'task_pending_job_expired_local', ?, ?, ?, ?, '[]', 'pending', ?, ?)
            """,
            (miner["miner_id"], "a" * 64, "b" * 64, "c" * 64, now, now),
        )

    job = get_validation_job(validator["validator_id"])

    assert job is not None
    assert job["job_id"] == "job_pending_with_expired_task"
    with get_connection() as connection:
        task = connection.execute(
            "SELECT status, expires_at FROM tasks WHERE task_id = 'task_pending_job_expired_local'"
        ).fetchone()
    assert task["status"] == "revealed"
    assert task["expires_at"] != "2026-06-01T00:10:00+00:00"


def test_validation_jobs_health_reports_stuck_partial_quorum(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "validation-health-stuck.sqlite3")
    miner = register_miner("health-miner", generate_keypair()["public_key"])
    first_keys = generate_keypair()
    second_keys = generate_keypair()
    first = register_validator("health-validator-one", first_keys["public_key"])
    second = register_validator("health-validator-two", second_keys["public_key"])
    record_validator_heartbeat(_signed_validator_heartbeat(first_keys, first["validator_id"], node_id="node-one"))
    record_validator_heartbeat(_signed_validator_heartbeat(second_keys, second["validator_id"], node_id="node-two"))

    old_time = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at
            )
            VALUES ('task_health_stuck', ?, 1000, 1063, 'bbp_hex_v1', 'revealed', ?, ?)
            """,
            (miner["miner_id"], protocol_params_id, old_time),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at, job_created_at, first_vote_at
            )
            VALUES ('job_health_stuck', 'task_health_stuck', ?, ?, ?, ?, '[]', 'pending', ?, ?, ?)
            """,
            (miner["miner_id"], "a" * 64, "b" * 64, "c" * 64, old_time, old_time, old_time),
        )
        connection.execute(
            """
            INSERT INTO validation_votes (
                job_id, task_id, validator_id, approved, reason, signature,
                signed_at, validation_ms, created_at
            )
            VALUES ('job_health_stuck', 'task_health_stuck', ?, 1, 'ok', 'sig', ?, 1, ?)
            """,
            (first["validator_id"], old_time, old_time),
        )

    health = get_validation_jobs_health(stale_after_seconds=120)

    assert health["healthy"] is False
    assert health["stuck_count"] == 1
    assert health["counts"]["stuck_waiting_for_quorum"] == 1
    assert health["jobs"][0]["job_id"] == "job_health_stuck"
    assert health["jobs"][0]["health"] == "stuck_waiting_for_quorum"
    assert health["jobs"][0]["voted_validator_ids"] == [first["validator_id"]]
    assert health["jobs"][0]["missing_eligible_validator_ids"] == [second["validator_id"]]
    assert health["jobs"][0]["missing_eligible_validators"][0]["node_id"] == "node-two"


def test_validation_jobs_health_releases_timed_out_assignment_without_validator_poll(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "validation-health-release-assignment.sqlite3")
    monkeypatch.setattr("app.services.mining._EXPIRED_TASK_CLEANUP_LAST_RUN_MONOTONIC", 0.0)
    miner = register_miner("health-release-miner", generate_keypair()["public_key"])
    first_keys = generate_keypair()
    second_keys = generate_keypair()
    first = register_validator("health-release-validator-one", first_keys["public_key"])
    second = register_validator("health-release-validator-two", second_keys["public_key"])
    record_validator_heartbeat(_signed_validator_heartbeat(first_keys, first["validator_id"], node_id="node-one"))
    record_validator_heartbeat(_signed_validator_heartbeat(second_keys, second["validator_id"], node_id="node-two"))

    old_time = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at
            )
            VALUES ('task_health_release', ?, 1000, 1063, 'bbp_hex_v1', 'revealed', ?, ?)
            """,
            (miner["miner_id"], protocol_params_id, old_time),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, assigned_validator_id, assigned_at, created_at, job_created_at
            )
            VALUES ('job_health_release', 'task_health_release', ?, ?, ?, ?, '[]', 'pending', ?, ?, ?, ?)
            """,
            (miner["miner_id"], "a" * 64, "b" * 64, "c" * 64, first["validator_id"], old_time, old_time, old_time),
        )

    health = get_validation_jobs_health(stale_after_seconds=120)

    assert health["counts"]["assignment_timeout_pending_release"] == 0
    assert health["counts"]["stuck_no_votes"] == 1
    assert health["jobs"][0]["job_id"] == "job_health_release"
    assert health["jobs"][0]["assigned_validator_id"] is None
    assert health["jobs"][0]["blocking_reason"] == "assigned_validator_timeout"
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT assigned_validator_id, assigned_at, assignment_failures, blocking_reason
            FROM validation_jobs
            WHERE job_id = 'job_health_release'
            """
        ).fetchone()
    assert row["assigned_validator_id"] is None
    assert row["assigned_at"] is None
    assert row["assignment_failures"] == 1
    assert row["blocking_reason"] == "assigned_validator_timeout"


def test_validation_jobs_health_treats_recent_pending_as_healthy(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "validation-health-recent.sqlite3")
    miner = register_miner("recent-health-miner", generate_keypair()["public_key"])
    keys = generate_keypair()
    validator = register_validator("recent-health-validator", keys["public_key"])
    record_validator_heartbeat(_signed_validator_heartbeat(keys, validator["validator_id"]))

    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at
            )
            VALUES ('task_health_recent', ?, 1000, 1063, 'bbp_hex_v1', 'revealed', ?, ?)
            """,
            (miner["miner_id"], protocol_params_id, now),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at, job_created_at
            )
            VALUES ('job_health_recent', 'task_health_recent', ?, ?, ?, ?, '[]', 'pending', ?, ?)
            """,
            (miner["miner_id"], "a" * 64, "b" * 64, "c" * 64, now, now),
        )

    health = get_validation_jobs_health(stale_after_seconds=120)

    assert health["healthy"] is True
    assert health["pending_count"] == 1
    assert health["stuck_count"] == 0
    assert health["counts"]["pending_recent"] == 1
    assert health["jobs"][0]["voted_validator_ids"] == []
    assert health["jobs"][0]["missing_eligible_validator_ids"] == [validator["validator_id"]]


def test_revealed_task_does_not_expire_while_quorum_can_still_advance(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "revealed-quorum-path.sqlite3")
    miner = register_miner("quorum-path-miner", generate_keypair()["public_key"])
    first_keys = generate_keypair()
    second_keys = generate_keypair()
    first = register_validator("quorum-validator-one", first_keys["public_key"])
    second = register_validator("quorum-validator-two", second_keys["public_key"])
    record_validator_heartbeat(_signed_validator_heartbeat(first_keys, first["validator_id"], node_id="node-one"))
    record_validator_heartbeat(_signed_validator_heartbeat(second_keys, second["validator_id"], node_id="node-two"))

    old_time = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, expires_at
            )
            VALUES ('task_quorum_path', ?, 1000, 1063, 'bbp_hex_v1', 'revealed', ?, ?, ?)
            """,
            (miner["miner_id"], protocol_params_id, old_time, old_time),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at
            )
            VALUES ('job_quorum_path', 'task_quorum_path', ?, ?, ?, ?, '[]', 'pending', ?)
            """,
            (miner["miner_id"], "a" * 64, "b" * 64, "c" * 64, old_time),
        )
        connection.execute(
            """
            INSERT INTO validation_votes (
                job_id, task_id, validator_id, approved, reason, signature,
                signed_at, validation_ms, created_at
            )
            VALUES ('job_quorum_path', 'task_quorum_path', ?, 1, 'ok', 'sig', ?, 1, ?)
            """,
            (first["validator_id"], old_time, old_time),
        )

    result = cleanup_expired_tasks()

    assert result["expired_tasks"] == 0
    with get_connection() as connection:
        task = connection.execute("SELECT status FROM tasks WHERE task_id = 'task_quorum_path'").fetchone()
        job = connection.execute("SELECT status FROM validation_jobs WHERE job_id = 'job_quorum_path'").fetchone()
    assert task["status"] == "revealed"
    assert job["status"] == "pending"


def test_revealed_task_with_no_votes_expires_after_validation_window(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "revealed-no-votes-expires.sqlite3")
    miner = register_miner("no-votes-miner", generate_keypair()["public_key"])
    keys = generate_keypair()
    validator = register_validator("no-votes-validator", keys["public_key"])
    record_validator_heartbeat(_signed_validator_heartbeat(keys, validator["validator_id"], node_id="node-one"))

    old_time = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, expires_at
            )
            VALUES ('task_no_votes_expired', ?, 1000, 1063, 'bbp_hex_v1', 'revealed', ?, ?, ?)
            """,
            (miner["miner_id"], protocol_params_id, old_time, old_time),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at
            )
            VALUES ('job_no_votes_expired', 'task_no_votes_expired', ?, ?, ?, ?, '[]', 'pending', ?)
            """,
            (miner["miner_id"], "a" * 64, "b" * 64, "c" * 64, old_time),
        )

    result = cleanup_expired_tasks()

    assert result["expired_tasks"] == 1
    assert result["expired_validation_jobs"] == 1
    with get_connection() as connection:
        task = connection.execute("SELECT status FROM tasks WHERE task_id = 'task_no_votes_expired'").fetchone()
        job = connection.execute("SELECT status FROM validation_jobs WHERE job_id = 'job_no_votes_expired'").fetchone()
    assert task["status"] == "expired"
    assert job["status"] == "expired"
