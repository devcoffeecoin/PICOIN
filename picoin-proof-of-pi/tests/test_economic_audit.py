import pytest

from app.core.crypto import hash_result
from app.core.pi import calculate_pi_segment
from app.core.signatures import build_submission_signature_payload, build_validation_result_signature_payload, generate_keypair, sign_payload
from app.db.database import get_connection, init_db
from app.services.mining import (
    create_next_task,
    get_balance,
    get_balance_amount,
    get_block,
    get_full_economic_audit,
    register_miner,
    register_validator,
    request_faucet,
    submit_validation_result,
    submit_task,
)
from app.services.transactions import canonical_empty_tx_merkle_root, canonical_selected_tx_hashes_hash
from app.services.wallet import create_wallet


def test_full_economic_audit_passes_for_valid_local_economy(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "economic-valid.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner, miner_keys = _register_miner_with_keys("audit-miner")
    validator_keys = generate_keypair()
    register_validator("audit-validator", validator_keys["public_key"])
    request_faucet(miner["miner_id"], "miner", 1.0)
    _mine_legacy_block(miner["miner_id"], miner_keys["private_key"])

    audit = get_full_economic_audit()

    assert audit["valid"] is True
    assert audit["issues"] == []
    assert audit["supply"]["expected_total_balances"] == pytest.approx(3.1416 + 2.51328 + 0.219912 + 0.094248)
    assert audit["rewards"]["accepted_blocks"] == 1
    assert audit["rewards"]["block_reward_total"] == 2.51328
    assert audit["rewards"]["science_reserve_total"] == 0.219912
    assert audit["rewards"]["scientific_development_treasury_total"] == 0.094248
    assert audit["ledger"]["account_mismatch_count"] == 0
    assert audit["validators"]["stake_locked"] == 31.416


def test_full_economic_audit_detects_tampered_balance(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "economic-tampered.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner, _ = _register_miner_with_keys("tampered-miner")
    request_faucet(miner["miner_id"], "miner", 1.0)
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


def test_full_economic_audit_includes_additional_validator_rewards(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "economic-validator-reward.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner, _ = _register_miner_with_keys("validator-reward-miner")
    first_keys = generate_keypair()
    second_keys = generate_keypair()
    third_keys = generate_keypair()
    first_validator = register_validator("validator-reward-one", first_keys["public_key"])
    second_validator = register_validator("validator-reward-two", second_keys["public_key"])
    third_validator = register_validator("validator-reward-three", third_keys["public_key"])
    job_id, task_id = _insert_validation_job(miner["miner_id"])

    _submit_vote(job_id, task_id, first_validator["validator_id"], first_keys["private_key"], "first")
    _submit_vote(job_id, task_id, second_validator["validator_id"], second_keys["private_key"], "second")
    response = _submit_vote(job_id, task_id, third_validator["validator_id"], third_keys["private_key"], "third")

    audit = get_full_economic_audit()

    assert response["status"] == "approved"
    assert response["block"]["validator_reward"]["pool"] == 0.31416
    assert audit["valid"] is True
    assert audit["supply"]["expected_total_balances"] == pytest.approx(
        3.1416 + 2.51328 + 0.31416 + 0.219912 + 0.094248
    )
    assert audit["rewards"]["block_reward_total"] == 2.51328
    assert audit["rewards"]["validator_reward_total"] == 0.31416
    assert audit["rewards"]["science_reserve_total"] == 0.219912
    assert audit["rewards"]["scientific_development_treasury_total"] == 0.094248
    assert audit["rewards"]["total_minted_rewards"] == 3.1416


def test_miner_reward_address_receives_new_block_rewards(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "economic-miner-reward-address.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner_keys = generate_keypair()
    reward_wallet = create_wallet("miner-reward")
    miner = register_miner("reward-address-miner", miner_keys["public_key"], reward_wallet["address"])

    _mine_legacy_block(miner["miner_id"], miner_keys["private_key"])
    block = get_block(1)
    audit = get_full_economic_audit()

    assert block is not None
    assert block["miner_reward_address"] == reward_wallet["address"]
    assert get_balance_amount(reward_wallet["address"]) == pytest.approx(block["reward"])
    assert get_balance_amount(miner["miner_id"]) == pytest.approx(0)
    assert audit["valid"] is True


def test_block_rewards_can_mature_after_configured_depth(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "economic-block-maturity.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.services.rewards.BLOCK_MATURITY_DEPTH", 2)
    monkeypatch.setattr("app.services.mining.BLOCK_MATURITY_DEPTH", 2)
    init_db(db_path)

    miner, miner_keys = _register_miner_with_keys("maturity-miner")

    _mine_legacy_block(miner["miner_id"], miner_keys["private_key"])
    first_balance = get_balance(miner["miner_id"])
    first_audit = get_full_economic_audit()

    assert first_balance is not None
    assert first_balance["available_balance"] == pytest.approx(0)
    assert first_balance["immature_rewards"] == pytest.approx(2.51328)
    assert first_audit["valid"] is True
    assert first_audit["rewards"]["mature_block_reward_total"] == pytest.approx(0)
    assert first_audit["rewards"]["immature_block_reward_total"] == pytest.approx(2.51328)

    _mine_legacy_block(miner["miner_id"], miner_keys["private_key"])
    second_balance = get_balance(miner["miner_id"])

    assert second_balance is not None
    assert second_balance["available_balance"] == pytest.approx(0)
    assert second_balance["immature_rewards"] == pytest.approx(5.02656)

    _mine_legacy_block(miner["miner_id"], miner_keys["private_key"])
    third_balance = get_balance(miner["miner_id"])
    final_audit = get_full_economic_audit()

    assert third_balance is not None
    assert third_balance["available_balance"] == pytest.approx(2.51328)
    assert third_balance["immature_rewards"] == pytest.approx(5.02656)
    assert third_balance["total_balance"] == pytest.approx(7.53984)
    assert final_audit["valid"] is True
    assert final_audit["rewards"]["block_maturity_depth"] == 2
    assert final_audit["rewards"]["block_reward_total"] == pytest.approx(7.53984)
    assert final_audit["rewards"]["mature_block_reward_total"] == pytest.approx(2.51328)
    assert final_audit["rewards"]["immature_block_reward_total"] == pytest.approx(5.02656)


def test_validator_reward_address_receives_new_validator_rewards(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "economic-validator-reward-address.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner, _ = _register_miner_with_keys("validator-wallet-miner")
    first_keys = generate_keypair()
    second_keys = generate_keypair()
    third_keys = generate_keypair()
    first_reward = create_wallet("validator-one-reward")
    second_reward = create_wallet("validator-two-reward")
    third_reward = create_wallet("validator-three-reward")
    first_validator = register_validator("validator-wallet-one", first_keys["public_key"], first_reward["address"])
    second_validator = register_validator("validator-wallet-two", second_keys["public_key"], second_reward["address"])
    third_validator = register_validator("validator-wallet-three", third_keys["public_key"], third_reward["address"])
    job_id, task_id = _insert_validation_job(miner["miner_id"])

    _submit_vote(job_id, task_id, first_validator["validator_id"], first_keys["private_key"], "first")
    _submit_vote(job_id, task_id, second_validator["validator_id"], second_keys["private_key"], "second")
    response = _submit_vote(job_id, task_id, third_validator["validator_id"], third_keys["private_key"], "third")
    audit = get_full_economic_audit()

    reward_addresses = response["block"]["validator_reward"]["reward_addresses"]
    per_validator = response["block"]["validator_reward"]["per_validator"]
    assert response["status"] == "approved"
    assert reward_addresses[first_validator["validator_id"]] == first_reward["address"]
    assert reward_addresses[second_validator["validator_id"]] == second_reward["address"]
    assert reward_addresses[third_validator["validator_id"]] == third_reward["address"]
    assert get_balance_amount(first_reward["address"]) == pytest.approx(per_validator)
    assert get_balance_amount(first_validator["validator_id"]) == pytest.approx(0)
    assert audit["valid"] is True


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


def _insert_validation_job(miner_id: str) -> tuple[str, str]:
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        task_id = "task_validator_reward"
        job_id = "job_validator_reward"
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, selected_tx_hashes, tx_merkle_root, tx_count,
                tx_fee_total_units, selected_tx_hashes_hash, created_at
            )
            VALUES (?, ?, 2000, 2063, 'bbp_hex_v1', 'revealed', ?, '[]', ?, 0, 0, ?, ?)
            """,
            (
                task_id,
                miner_id,
                protocol_params_id,
                canonical_empty_tx_merkle_root(),
                canonical_selected_tx_hashes_hash([]),
                "2026-05-10T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '[]', 'pending', ?)
            """,
            (job_id, task_id, miner_id, "a" * 64, "b" * 64, "c" * 64, "2026-05-10T00:00:00+00:00"),
        )
    return job_id, task_id


def _submit_vote(job_id: str, task_id: str, validator_id: str, private_key: str, label: str) -> dict:
    signed_at_by_label = {"first": 1, "second": 2, "third": 3}
    signed_at = f"2026-05-10T00:00:0{signed_at_by_label[label]}+00:00"
    reason = f"accepted by {label}"
    signature = sign_payload(
        private_key,
        build_validation_result_signature_payload(
            job_id=job_id,
            validator_id=validator_id,
            task_id=task_id,
            approved=True,
            reason=reason,
            signed_at=signed_at,
        ),
    )
    return submit_validation_result(job_id, validator_id, True, reason, signature, signed_at)
