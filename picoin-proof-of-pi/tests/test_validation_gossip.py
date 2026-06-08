from datetime import datetime, timezone

import pytest

from app.core.signatures import build_validation_result_signature_payload, generate_keypair, sign_payload
from app.db.database import get_connection, init_db
from app.services import mining as mining_service
from app.services.mining import (
    MiningError,
    list_validation_job_inventory,
    receive_validation_job_gossip,
    receive_validation_vote_gossip,
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


def _insert_revealed_validation_job() -> dict[str, str]:
    miner = register_miner("gossip-miner", generate_keypair()["public_key"])
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
            VALUES ('task_gossip', ?, 1000, 1063, 'bbp_hex_v1', 'revealed', ?, ?)
            """,
            (miner["miner_id"], protocol_params_id, now),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at, job_created_at
            )
            VALUES ('job_gossip', 'task_gossip', ?, ?, ?, ?, '[]', 'pending', ?, ?)
            """,
            (miner["miner_id"], "a" * 64, "b" * 64, "c" * 64, now, now),
        )
    return {"miner_id": miner["miner_id"], "task_id": "task_gossip", "job_id": "job_gossip"}


def test_validation_job_gossip_imports_task_and_miner(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "source.sqlite3")
    ids = _insert_revealed_validation_job()
    envelope = list_validation_job_inventory()["jobs"][0]

    _use_db(tmp_path, monkeypatch, "target.sqlite3")
    result = receive_validation_job_gossip(envelope, source_peer="http://node-a")
    duplicate = receive_validation_job_gossip(envelope, source_peer="http://node-a")

    assert result["status"] == "accepted"
    assert duplicate["status"] == "duplicate"
    with get_connection() as connection:
        task = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (ids["task_id"],)).fetchone()
        job = connection.execute("SELECT * FROM validation_jobs WHERE job_id = ?", (ids["job_id"],)).fetchone()
        miner = connection.execute("SELECT * FROM miners WHERE miner_id = ?", (ids["miner_id"],)).fetchone()

    assert task is not None
    assert task["status"] == "revealed"
    assert job is not None
    assert job["status"] == "pending"
    assert miner is not None


def test_validation_vote_gossip_requires_valid_signature_and_is_idempotent(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "source-vote.sqlite3")
    ids = _insert_revealed_validation_job()
    envelope = list_validation_job_inventory()["jobs"][0]

    _use_db(tmp_path, monkeypatch, "target-vote.sqlite3")
    receive_validation_job_gossip(envelope, source_peer="http://node-a")
    validator_keys = generate_keypair()
    validator = register_validator("gossip-validator", validator_keys["public_key"])
    signed_at = datetime.now(timezone.utc).isoformat()
    signature_payload = build_validation_result_signature_payload(
        job_id=ids["job_id"],
        validator_id=validator["validator_id"],
        task_id=ids["task_id"],
        approved=False,
        reason="invalid sample",
        signed_at=signed_at,
    )
    vote = {
        "vote": {
            "job_id": ids["job_id"],
            "task_id": ids["task_id"],
            "validator_id": validator["validator_id"],
            "approved": False,
            "reason": "invalid sample",
            "signature": sign_payload(validator_keys["private_key"], signature_payload),
            "signed_at": signed_at,
            "validation_ms": 7,
            "created_at": signed_at,
        }
    }

    result = receive_validation_vote_gossip(vote, source_peer="http://node-b")
    duplicate = receive_validation_vote_gossip(vote, source_peer="http://node-b")

    assert result["status"] == "accepted"
    assert duplicate["status"] == "duplicate"
    with get_connection() as connection:
        count = connection.execute("SELECT COUNT(*) AS count FROM validation_votes").fetchone()["count"]
    assert count == 1

    bad_keys = generate_keypair()
    bad_validator = register_validator("bad-gossip-validator", bad_keys["public_key"])
    bad_signed_at = datetime.now(timezone.utc).isoformat()
    bad_vote = {
        "vote": {
            "job_id": ids["job_id"],
            "task_id": ids["task_id"],
            "validator_id": bad_validator["validator_id"],
            "approved": True,
            "reason": "ok",
            "signature": "bad-signature",
            "signed_at": bad_signed_at,
            "validation_ms": 1,
            "created_at": bad_signed_at,
        }
    }
    with pytest.raises(MiningError):
        receive_validation_vote_gossip(bad_vote, source_peer="http://node-b")
