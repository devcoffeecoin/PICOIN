from datetime import datetime, timedelta, timezone

import pytest

from app.core.signatures import generate_keypair, sign_payload
from app.db.database import get_connection, init_db
from app.services.mining import (
    MiningError,
    get_validation_job,
    get_validators,
    record_validator_heartbeat,
    refresh_participant_liveness,
    register_miner,
    register_validator,
)


def _use_db(tmp_path, monkeypatch, name: str):
    db_path = tmp_path / name
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
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
    refresh_participant_liveness(stale_time)
    with get_connection() as connection:
        row = connection.execute("SELECT online_status FROM validators WHERE validator_id = 'validator_live'").fetchone()
    assert row["online_status"] == "stale"

    offline_time = datetime.now(timezone.utc) + timedelta(seconds=301)
    refresh_participant_liveness(offline_time)
    with get_connection() as connection:
        row = connection.execute("SELECT online_status FROM validators WHERE validator_id = 'validator_live'").fetchone()
    assert row["online_status"] == "offline"


def test_offline_validator_is_excluded_from_quorum_eligibility(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "eligibility.sqlite3")
    validator = register_validator("eligible-now", generate_keypair()["public_key"])
    assert validator["validator_id"] in {item["validator_id"] for item in get_validators(eligible_only=True)}

    with get_connection() as connection:
        connection.execute(
            "UPDATE validators SET last_heartbeat_at = ? WHERE validator_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat(), validator["validator_id"]),
        )
    refresh_participant_liveness()

    assert validator["validator_id"] not in {item["validator_id"] for item in get_validators(eligible_only=True)}


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
    first = register_validator("validator-one", generate_keypair()["public_key"])
    second = register_validator("validator-two", generate_keypair()["public_key"])

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
    first = register_validator("parallel-validator-one", generate_keypair()["public_key"])
    second = register_validator("parallel-validator-two", generate_keypair()["public_key"])

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
