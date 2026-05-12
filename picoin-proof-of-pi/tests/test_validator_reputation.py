import pytest

from app.core.signatures import build_validation_result_signature_payload, generate_keypair, sign_payload
from app.db.database import get_connection, init_db
from app.services.mining import (
    MiningError,
    get_audit_summary,
    get_balance,
    get_ledger_entries,
    get_validator,
    get_validators,
    register_miner,
    register_validator,
    submit_validation_result,
)


def test_validator_reputation_tracks_completed_approved_jobs(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "validator-approved.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner_id = _register_miner("miner-ok")
    validator_keys = generate_keypair()
    validator = register_validator("validator-ok", validator_keys["public_key"])
    job_id, task_id = _insert_validation_job(miner_id, validator["validator_id"])
    signed_at = "2026-05-10T00:00:00+00:00"
    reason = "accepted samples"
    signature = _sign_validation_result(
        validator_keys["private_key"],
        job_id,
        validator["validator_id"],
        task_id,
        True,
        reason,
        signed_at,
    )

    response = submit_validation_result(
        job_id=job_id,
        validator_id=validator["validator_id"],
        approved=True,
        reason=reason,
        signature=signature,
        signed_at=signed_at,
    )
    updated = get_validator(validator["validator_id"])

    assert response["accepted"] is True
    assert response["status"] == "validation_pending"
    assert response["approvals"] == 1
    assert response["required_approvals"] == 3
    assert response["block"] is None
    assert updated["accepted_jobs"] == 1
    assert updated["completed_jobs"] == 1
    assert updated["invalid_results"] == 0
    assert updated["trust_score"] == 1.0
    assert updated["avg_validation_ms"] >= 0


def test_block_is_accepted_after_validator_quorum(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "validator-quorum.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner_id = _register_miner("miner-quorum")
    first_keys = generate_keypair()
    second_keys = generate_keypair()
    third_keys = generate_keypair()
    first_validator = register_validator("validator-one", first_keys["public_key"])
    second_validator = register_validator("validator-two", second_keys["public_key"])
    third_validator = register_validator("validator-three", third_keys["public_key"])
    job_id, task_id = _insert_validation_job(miner_id, first_validator["validator_id"])
    signed_at = "2026-05-10T00:00:00+00:00"

    first_signature = _sign_validation_result(
        first_keys["private_key"],
        job_id,
        first_validator["validator_id"],
        task_id,
        True,
        "accepted by first",
        signed_at,
    )
    first_response = submit_validation_result(
        job_id=job_id,
        validator_id=first_validator["validator_id"],
        approved=True,
        reason="accepted by first",
        signature=first_signature,
        signed_at=signed_at,
    )

    second_signature = _sign_validation_result(
        second_keys["private_key"],
        job_id,
        second_validator["validator_id"],
        task_id,
        True,
        "accepted by second",
        signed_at,
    )
    second_response = submit_validation_result(
        job_id=job_id,
        validator_id=second_validator["validator_id"],
        approved=True,
        reason="accepted by second",
        signature=second_signature,
        signed_at=signed_at,
    )

    third_signature = _sign_validation_result(
        third_keys["private_key"],
        job_id,
        third_validator["validator_id"],
        task_id,
        True,
        "accepted by third",
        signed_at,
    )
    third_response = submit_validation_result(
        job_id=job_id,
        validator_id=third_validator["validator_id"],
        approved=True,
        reason="accepted by third",
        signature=third_signature,
        signed_at=signed_at,
    )

    assert first_response["status"] == "validation_pending"
    assert second_response["status"] == "validation_pending"
    assert third_response["status"] == "approved"
    assert third_response["approvals"] == 3
    assert third_response["required_approvals"] == 3
    assert third_response["block"] is not None
    miner_balance = get_balance(miner_id)
    first_validator_balance = get_balance(first_validator["validator_id"])
    second_validator_balance = get_balance(second_validator["validator_id"])
    third_validator_balance = get_balance(third_validator["validator_id"])
    ledger = get_ledger_entries(miner_id)
    assert miner_balance["balance"] == 2.104872
    assert first_validator_balance["balance"] == 0.10472
    assert second_validator_balance["balance"] == 0.10472
    assert third_validator_balance["balance"] == 0.10472
    assert ledger[0]["entry_type"] == "block_reward"
    assert get_validator(first_validator["validator_id"])["accepted_jobs"] == 1
    assert get_validator(first_validator["validator_id"])["total_rewards"] == 0.10472
    assert get_validator(second_validator["validator_id"])["accepted_jobs"] == 1
    assert get_validator(second_validator["validator_id"])["total_rewards"] == 0.10472
    assert get_validator(third_validator["validator_id"])["accepted_jobs"] == 1
    assert get_validator(third_validator["validator_id"])["total_rewards"] == 0.10472


def test_genesis_balance_and_validator_stake_are_persisted(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "economy.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keys = generate_keypair()
    validator = register_validator("stake-validator", keys["public_key"])
    genesis = get_balance("genesis")
    validator_balance = get_balance(validator["validator_id"])
    audit = get_audit_summary()
    eligible = get_validators(eligible_only=True)

    assert genesis["balance"] == 3.1416
    assert validator_balance["balance"] == 0.0
    assert validator["stake_locked"] == 31.416
    assert audit["genesis_supply"] == 3.1416
    assert audit["eligible_validator_count"] == 1
    assert eligible[0]["validator_id"] == validator["validator_id"]


def test_validator_invalid_signatures_reduce_trust_and_trigger_cooldown(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "validator-invalid.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner_id = _register_miner("miner-bad-validator")
    validator_keys = generate_keypair()
    validator = register_validator("validator-bad", validator_keys["public_key"])

    for index in range(3):
        job_id, _ = _insert_validation_job(miner_id, validator["validator_id"], suffix=str(index))
        with pytest.raises(MiningError):
            submit_validation_result(
                job_id=job_id,
                validator_id=validator["validator_id"],
                approved=True,
                reason="bad signature",
                signature="invalid-signature",
                signed_at="2026-05-10T00:00:00+00:00",
            )

    updated = get_validator(validator["validator_id"])

    assert updated["invalid_results"] == 3
    assert updated["trust_score"] < 1.0
    assert updated["cooldown_until"] is not None
    assert updated["stake_locked"] < 31.416
    assert updated["slashed_amount"] == 9.4248
    assert updated["is_banned"] is False


def _register_miner(name: str) -> str:
    keys = generate_keypair()
    miner = register_miner(name, keys["public_key"])
    return miner["miner_id"]


def _insert_validation_job(miner_id: str, validator_id: str, suffix: str = "1") -> tuple[str, str]:
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        task_id = f"task_validator_rep_{suffix}"
        job_id = f"job_validator_rep_{suffix}"
        range_start = 1000 + (int(suffix) * 100 if suffix.isdigit() else 0)
        range_end = range_start + 63
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at
            )
            VALUES (?, ?, ?, ?, 'bbp_hex_v1', 'revealed', ?, ?)
            """,
            (task_id, miner_id, range_start, range_end, protocol_params_id, "2026-05-10T00:00:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, assigned_validator_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '[]', 'pending', ?, ?)
            """,
            (
                job_id,
                task_id,
                miner_id,
                f"{1000 + len(suffix):064x}",
                "b" * 64,
                "c" * 64,
                validator_id,
                "2026-05-10T00:00:00+00:00",
            ),
        )
    return job_id, task_id


def _sign_validation_result(
    private_key: str,
    job_id: str,
    validator_id: str,
    task_id: str,
    approved: bool,
    reason: str,
    signed_at: str,
) -> str:
    payload = build_validation_result_signature_payload(
        job_id=job_id,
        validator_id=validator_id,
        task_id=task_id,
        approved=approved,
        reason=reason,
        signed_at=signed_at,
    )
    return sign_payload(private_key, payload)
