from datetime import datetime, timedelta, timezone

import pytest

from app.core.crypto import hash_result
from app.core.merkle import merkle_proof, merkle_root, verify_merkle_proof
from app.core.pi import calculate_pi_segment
from app.core.settings import FAUCET_RATE_LIMIT_MAX_REQUESTS, TASK_RATE_LIMIT_MAX_ASSIGNMENTS
from app.core.signatures import (
    build_commit_signature_payload,
    build_reveal_signature_payload,
    build_validation_result_signature_payload,
    generate_keypair,
    sign_payload,
)
from app.db.database import get_connection, init_db
from app.services import mining
from app.services.mining import (
    MiningError,
    cleanup_expired_tasks,
    commit_task,
    create_next_task,
    get_protocol,
    get_validation_job,
    register_miner,
    register_validator,
    request_faucet,
    reveal_task,
    submit_validation_result,
)


def test_faucet_is_disabled_outside_local_network(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "network-faucet.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining, "NETWORK_ID", "testnet")
    init_db(db_path)

    miner, _ = _register_miner_with_keys("network-miner")

    protocol = get_protocol()
    assert protocol["network_id"] == "testnet"
    assert protocol["faucet_enabled"] is False
    with pytest.raises(MiningError) as exc:
        request_faucet(miner["miner_id"], "miner", 1.0)
    assert exc.value.status_code == 403


def test_faucet_rate_limits_repeated_demo_credits(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "faucet-rate-limit.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner, _ = _register_miner_with_keys("faucet-limited-miner")
    for _ in range(FAUCET_RATE_LIMIT_MAX_REQUESTS):
        request_faucet(miner["miner_id"], "miner", 1.0)

    with pytest.raises(MiningError) as exc:
        request_faucet(miner["miner_id"], "miner", 1.0)
    assert exc.value.status_code == 429


def test_task_assignment_rate_limit_rejects_assignment_spam(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "task-rate-limit.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner, _ = _register_miner_with_keys("task-limited-miner")
    _insert_recent_tasks(miner["miner_id"], TASK_RATE_LIMIT_MAX_ASSIGNMENTS)

    with pytest.raises(MiningError) as exc:
        create_next_task(miner["miner_id"])
    assert exc.value.status_code == 429


def test_cleanup_expires_stale_tasks_and_validation_jobs(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "cleanup.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner, _ = _register_miner_with_keys("cleanup-miner")
    _insert_expired_validation_job(miner["miner_id"])

    result = cleanup_expired_tasks()

    assert result["expired_tasks"] == 1
    assert result["expired_validation_jobs"] == 1
    with get_connection() as connection:
        task_status = connection.execute("SELECT status FROM tasks WHERE task_id = 'task_cleanup'").fetchone()["status"]
        job_status = connection.execute("SELECT status FROM validation_jobs WHERE job_id = 'job_cleanup'").fetchone()["status"]
    assert task_status == "expired"
    assert job_status == "expired"


def test_full_commit_reveal_flow_accepts_block_after_two_validator_votes(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "full-flow.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner, miner_keys = _register_miner_with_keys("full-flow-miner")
    first_validator, first_keys = _register_validator_with_keys("flow-validator-one")
    second_validator, second_keys = _register_validator_with_keys("flow-validator-two")

    task = create_next_task(miner["miner_id"])
    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    root = merkle_root(segment, task["range_start"])

    commit_signed_at = "2026-05-10T00:00:00+00:00"
    commit_signature = sign_payload(
        miner_keys["private_key"],
        build_commit_signature_payload(
            task_id=task["task_id"],
            miner_id=miner["miner_id"],
            range_start=task["range_start"],
            range_end=task["range_end"],
            algorithm=task["algorithm"],
            result_hash=result_hash,
            merkle_root=root,
            signed_at=commit_signed_at,
        ),
    )
    challenge = commit_task(task["task_id"], miner["miner_id"], result_hash, root, commit_signature, commit_signed_at, 1)
    assert challenge["accepted"] is True

    revealed_samples = [
        {
            "position": sample["position"],
            "digit": segment[sample["position"] - task["range_start"]],
            "proof": merkle_proof(segment, task["range_start"], sample["position"]),
        }
        for sample in challenge["samples"]
    ]
    reveal_signed_at = "2026-05-10T00:00:01+00:00"
    reveal_signature = sign_payload(
        miner_keys["private_key"],
        build_reveal_signature_payload(
            task_id=task["task_id"],
            miner_id=miner["miner_id"],
            merkle_root=root,
            challenge_seed=challenge["challenge_seed"],
            signed_at=reveal_signed_at,
        ),
    )
    reveal = reveal_task(task["task_id"], miner["miner_id"], revealed_samples, reveal_signature, reveal_signed_at)
    assert reveal["status"] == "validation_pending"

    first_response = _vote_next_job(first_validator["validator_id"], first_keys["private_key"])
    second_response = _vote_next_job(second_validator["validator_id"], second_keys["private_key"])

    assert first_response["status"] == "validation_pending"
    assert second_response["status"] == "approved"
    assert second_response["block"]["height"] == 1
    assert second_response["approvals"] == 2


def _register_miner_with_keys(name: str) -> tuple[dict, dict]:
    keypair = generate_keypair()
    return register_miner(name, keypair["public_key"]), keypair


def _register_validator_with_keys(name: str) -> tuple[dict, dict]:
    keypair = generate_keypair()
    return register_validator(name, keypair["public_key"]), keypair


def _insert_recent_tasks(miner_id: str, count: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        for index in range(count):
            range_start = 1000 + (index * 100)
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, miner_id, range_start, range_end, algorithm, status,
                    protocol_params_id, created_at
                )
                VALUES (?, ?, ?, ?, 'bbp_hex_v1', 'expired', ?, ?)
                """,
                (f"task_recent_{index}", miner_id, range_start, range_start + 63, protocol_params_id, now),
            )


def _insert_expired_validation_job(miner_id: str) -> None:
    old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
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
            VALUES ('task_cleanup', ?, 1, 64, 'bbp_hex_v1', 'revealed', ?, ?, ?)
            """,
            (miner_id, protocol_params_id, old_time, old_time),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at
            )
            VALUES ('job_cleanup', 'task_cleanup', ?, ?, ?, ?, '[]', 'pending', ?)
            """,
            (miner_id, "a" * 64, "b" * 64, "c" * 64, old_time),
        )


def _vote_next_job(validator_id: str, private_key: str) -> dict:
    job = get_validation_job(validator_id)
    assert job is not None
    approved, reason = _validate_job_samples(job)
    signed_at = "2026-05-10T00:00:02+00:00"
    signature = sign_payload(
        private_key,
        build_validation_result_signature_payload(
            job_id=job["job_id"],
            validator_id=validator_id,
            task_id=job["task_id"],
            approved=approved,
            reason=reason,
            signed_at=signed_at,
        ),
    )
    return submit_validation_result(job["job_id"], validator_id, approved, reason, signature, signed_at)


def _validate_job_samples(job: dict) -> tuple[bool, str]:
    for sample in job["samples"]:
        position = sample["position"]
        digit = str(sample["digit"]).upper()
        expected = calculate_pi_segment(position, position, job["algorithm"])
        if digit != expected:
            return False, f"digit mismatch at position {position}"
        if not verify_merkle_proof(position, digit, sample["proof"], job["merkle_root"]):
            return False, f"invalid merkle proof at position {position}"
    return True, "external validator accepted samples"
