from app.core.crypto import hash_result
from app.core.pi import calculate_pi_segment
from app.core.signatures import build_submission_signature_payload, generate_keypair, sign_payload
from app.db.database import init_db
from app.db.database import get_connection
from app.services.mining import (
    create_next_task,
    get_balance,
    get_block,
    get_miner,
    get_recent_events,
    get_retroactive_audits,
    get_stats,
    get_validator,
    register_miner,
    register_validator,
    run_retroactive_audit,
    submit_task,
)


def test_retroactive_audit_uses_double_protocol_samples(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retroactive-audit.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("retro-miner", keypair["public_key"])
    _mine_legacy_block(miner["miner_id"], keypair["private_key"])

    result = run_retroactive_audit(sample_multiplier=2)
    audits = get_retroactive_audits()
    events = get_recent_events()

    assert result["accepted"] is True
    assert result["audit"]["sample_count"] == 64
    assert result["audit"]["passed"] is True
    assert len(result["audit"]["samples"]) == 64
    assert result["audit"]["automatic"] is False
    assert result["audit"]["reward"] == 0.0
    assert audits[0]["id"] == result["audit"]["id"]
    assert any(event["type"] == "retroactive_audit" for event in events)


def test_scheduled_retroactive_audit_runs_every_interval_and_mints_reward(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "scheduled-retroactive-audit.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.services.mining.RETROACTIVE_AUDIT_INTERVAL_BLOCKS", 1)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("scheduled-retro-miner", keypair["public_key"])
    _mine_legacy_block(miner["miner_id"], keypair["private_key"])

    audit = get_retroactive_audits()[0]
    stats = get_stats()
    audit_balance = get_balance("audit_treasury")

    assert audit["automatic"] is True
    assert audit["sample_count"] == 64
    assert audit["reward"] == 0.62832
    assert audit["reward_account_id"] == "audit_treasury"
    assert audit_balance["balance"] == 0.62832
    assert stats["total_audit_rewards"] == 0.62832


def test_failed_retroactive_audit_marks_fraud_and_hardens_penalties(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "fraud-retroactive-audit.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("fraud-retro-miner", keypair["public_key"])
    _mine_legacy_block(miner["miner_id"], keypair["private_key"])
    validators = [register_validator(f"fraud-validator-{index}", generate_keypair()["public_key"]) for index in range(3)]
    _attach_approved_votes(block_height=1, validator_ids=[validator["validator_id"] for validator in validators])

    with get_connection() as connection:
        connection.execute("UPDATE blocks SET result_hash = ? WHERE height = 1", ("f" * 64,))

    result = run_retroactive_audit(block_height=1, sample_multiplier=2)
    block = get_block(1)
    miner_after = get_miner(miner["miner_id"])
    validators_after = [get_validator(validator["validator_id"]) for validator in validators]

    assert result["accepted"] is False
    assert result["audit"]["fraud_detected"] is True
    assert block["fraudulent"] is True
    assert block["fraud_reason"] == "fraud detected: result_hash mismatch"
    assert miner_after["trust_score"] < 0.05
    assert miner_after["cooldown_until"] is not None
    assert all(validator["invalid_results"] == 3 for validator in validators_after)
    assert all(validator["trust_score"] < 0.5 for validator in validators_after)
    assert all(validator["cooldown_until"] is not None for validator in validators_after)


def _mine_legacy_block(miner_id: str, private_key: str) -> None:
    task = create_next_task(miner_id)
    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    signed_at = "2026-05-10T00:00:00+00:00"
    payload = build_submission_signature_payload(
        task_id=task["task_id"],
        miner_id=miner_id,
        range_start=task["range_start"],
        range_end=task["range_end"],
        algorithm=task["algorithm"],
        result_hash=result_hash,
        signed_at=signed_at,
    )
    signature = sign_payload(private_key, payload)
    response = submit_task(task["task_id"], miner_id, result_hash, segment, signature, signed_at)
    assert response["accepted"] is True


def _attach_approved_votes(block_height: int, validator_ids: list[str]) -> None:
    with get_connection() as connection:
        block = connection.execute("SELECT * FROM blocks WHERE height = ?", (block_height,)).fetchone()
        job_id = f"job_retro_fraud_{block_height}"
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?)
            """,
            (
                job_id,
                block["task_id"],
                block["miner_id"],
                block["result_hash"],
                block["merkle_root"] or "0" * 64,
                "0" * 64,
                block["samples"],
                "2026-05-10T00:00:00+00:00",
                "2026-05-10T00:00:01+00:00",
            ),
        )
        for validator_id in validator_ids:
            connection.execute(
                """
                INSERT INTO validation_votes (
                    job_id, task_id, validator_id, approved, reason, signature, signed_at,
                    validation_ms, created_at
                )
                VALUES (?, ?, ?, 1, 'test approval', 'signature', ?, 1, ?)
                """,
                (
                    job_id,
                    block["task_id"],
                    validator_id,
                    "2026-05-10T00:00:00+00:00",
                    "2026-05-10T00:00:00+00:00",
                ),
            )
