from app.core.crypto import hash_result
from app.core.pi import calculate_pi_segment
from app.core.signatures import build_submission_signature_payload, generate_keypair, sign_payload
from app.db.database import get_connection, init_db
from app.services.mining import (
    create_next_task,
    get_full_economic_audit,
    register_miner,
    register_validator,
    request_faucet,
    submit_task,
)


def test_full_economic_audit_passes_for_valid_local_economy(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "economic-valid.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner, miner_keys = _register_miner_with_keys("audit-miner")
    validator_keys = generate_keypair()
    register_validator("audit-validator", validator_keys["public_key"])
    request_faucet(miner["miner_id"], "miner", 10.0)
    _mine_legacy_block(miner["miner_id"], miner_keys["private_key"])

    audit = get_full_economic_audit()

    assert audit["valid"] is True
    assert audit["issues"] == []
    assert audit["supply"]["expected_total_balances"] == 3_141_600.0 + 3.1416
    assert audit["rewards"]["accepted_blocks"] == 1
    assert audit["rewards"]["block_reward_total"] == 3.1416
    assert audit["ledger"]["account_mismatch_count"] == 0
    assert audit["validators"]["stake_locked"] == 31.416


def test_full_economic_audit_detects_tampered_balance(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "economic-tampered.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner, _ = _register_miner_with_keys("tampered-miner")
    request_faucet(miner["miner_id"], "miner", 10.0)
    with get_connection() as connection:
        connection.execute(
            "UPDATE balances SET balance = balance + 1 WHERE account_id = ?",
            (miner["miner_id"],),
        )

    audit = get_full_economic_audit()
    issue_codes = {issue["code"] for issue in audit["issues"]}

    assert audit["valid"] is False
    assert "account_balance_mismatch" in issue_codes
    assert "total_balances_mismatch" in issue_codes


def _register_miner_with_keys(name: str) -> tuple[dict, dict]:
    keypair = generate_keypair()
    return register_miner(name, keypair["public_key"]), keypair


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
