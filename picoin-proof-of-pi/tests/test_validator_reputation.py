import pytest

from app.core.signatures import (
    build_validation_result_signature_payload,
    generate_keypair,
    sign_payload,
    verify_payload_signature,
)
from app.db.database import get_connection, init_db
from app.services.consensus import _import_finalized_block
from app.services.mining import (
    MiningError,
    _accept_block_in_connection,
    get_audit_summary,
    get_balance,
    get_block_finality_certificate,
    get_ledger_entries,
    get_validator,
    get_validators,
    record_validator_heartbeat,
    register_miner,
    register_validator,
    submit_validation_result,
)
from app.services.network import get_blocks_since
from app.services.transactions import canonical_empty_tx_merkle_root, canonical_selected_tx_hashes_hash


def test_validator_reputation_tracks_completed_approved_jobs(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "validator-approved.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner_id = _register_miner("miner-ok")
    validator_keys = generate_keypair()
    validator = register_validator("validator-ok", validator_keys["public_key"])
    record_validator_heartbeat(_signed_validator_heartbeat(validator_keys, validator["validator_id"]))
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
    assert response["status"] == "approved"
    assert response["approvals"] == 1
    assert response["required_approvals"] == 1
    assert response["block"] is not None
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
    finality_certificate = get_block_finality_certificate(third_response["block"]["height"])
    assert third_response["finality_certificate"] == finality_certificate
    assert finality_certificate is not None
    assert finality_certificate["block_height"] == third_response["block"]["height"]
    assert finality_certificate["block_hash"] == third_response["block"]["block_hash"]
    assert finality_certificate["task_id"] == task_id
    assert finality_certificate["job_id"] == job_id
    assert finality_certificate["required_approvals"] == 3
    assert finality_certificate["approval_count"] == 3
    assert finality_certificate["payload"]["version"] == "picoin-finality-v1"
    assert finality_certificate["payload"]["validation"]["status"] == "approved"
    assert finality_certificate["payload"]["block"]["state_root"] == third_response["block"]["state_root"]
    assert finality_certificate["payload"]["block"]["tx_count"] == 0
    assert len(finality_certificate["certificate_hash"]) == 64
    assert [vote["validator_id"] for vote in finality_certificate["votes"]] == [
        first_validator["validator_id"],
        second_validator["validator_id"],
        third_validator["validator_id"],
    ]
    for vote in finality_certificate["votes"]:
        assert "validator_reward_address" in vote
        assert verify_payload_signature(
            vote["validator_public_key"],
            vote["signature_payload"],
            vote["signature"],
        )
    miner_balance = get_balance(miner_id)
    first_validator_balance = get_balance(first_validator["validator_id"])
    second_validator_balance = get_balance(second_validator["validator_id"])
    third_validator_balance = get_balance(third_validator["validator_id"])
    ledger = get_ledger_entries(miner_id)
    assert miner_balance["balance"] == 2.51328
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


def test_finality_certificate_exports_and_imports_with_block_sync(tmp_path, monkeypatch) -> None:
    source_db_path = tmp_path / "validator-finality-source.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", source_db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", source_db_path)
    init_db(source_db_path)

    miner_id = _register_miner("miner-finality-sync")
    first_keys = generate_keypair()
    second_keys = generate_keypair()
    third_keys = generate_keypair()
    validators = [
        (first_keys, register_validator("sync-validator-one", first_keys["public_key"])),
        (second_keys, register_validator("sync-validator-two", second_keys["public_key"])),
        (third_keys, register_validator("sync-validator-three", third_keys["public_key"])),
    ]
    job_id, task_id = _insert_validation_job(miner_id, validators[0][1]["validator_id"], suffix="finality")
    signed_at = "2026-05-10T00:00:00+00:00"
    final_response = None
    for index, (keys, validator) in enumerate(validators, start=1):
        reason = f"accepted by sync validator {index}"
        final_response = submit_validation_result(
            job_id=job_id,
            validator_id=validator["validator_id"],
            approved=True,
            reason=reason,
            signature=_sign_validation_result(
                keys["private_key"],
                job_id,
                validator["validator_id"],
                task_id,
                True,
                reason,
                signed_at,
            ),
            signed_at=signed_at,
        )

    assert final_response is not None
    assert final_response["status"] == "approved"
    exported_block = get_blocks_since(0)["blocks"][0]
    assert exported_block["finality_certificate"]["certificate_hash"] == final_response["finality_certificate"]["certificate_hash"]

    target_db_path = tmp_path / "validator-finality-target.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", target_db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", target_db_path)
    init_db(target_db_path)
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO miners (miner_id, name, public_key, registered_at)
            VALUES ('miner-local-competing-range', 'local competing range', NULL, '2026-05-10T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, expires_at
            )
            VALUES (?, 'miner-local-competing-range', ?, ?, ?, 'assigned', ?, ?, ?)
            """,
            (
                "task_local_competing_same_range",
                exported_block["range_start"],
                exported_block["range_end"],
                exported_block["algorithm"],
                protocol_params_id,
                "2026-05-10T00:00:00+00:00",
                "2026-05-10T00:10:00+00:00",
            ),
        )
        imported = _import_finalized_block(connection, exported_block, "proposal_finality_sync")
        stored = connection.execute(
            "SELECT certificate_hash, block_hash, task_id, job_id FROM finality_certificates WHERE block_height = 1"
        ).fetchone()
        imported_task = connection.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        local_task = connection.execute(
            "SELECT status FROM tasks WHERE task_id = 'task_local_competing_same_range'"
        ).fetchone()

    assert imported is True
    assert stored is not None
    assert stored["certificate_hash"] == exported_block["finality_certificate"]["certificate_hash"]
    assert stored["block_hash"] == exported_block["block_hash"]
    assert stored["task_id"] == task_id
    assert stored["job_id"] == job_id
    assert imported_task["status"] == "accepted"
    assert local_task["status"] == "assigned"


def test_duplicate_validation_job_for_accepted_task_is_idempotent(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "validator-duplicate-finalization.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner_id = _register_miner("miner-duplicate-finalization")
    first_keys = generate_keypair()
    second_keys = generate_keypair()
    third_keys = generate_keypair()
    first_validator = register_validator("validator-one", first_keys["public_key"])
    second_validator = register_validator("validator-two", second_keys["public_key"])
    third_validator = register_validator("validator-three", third_keys["public_key"])
    job_id, task_id = _insert_validation_job(miner_id, first_validator["validator_id"], suffix="duplicate")
    signed_at = "2026-05-10T00:00:00+00:00"
    validators = [
        (first_keys, first_validator["validator_id"], "accepted by first"),
        (second_keys, second_validator["validator_id"], "accepted by second"),
        (third_keys, third_validator["validator_id"], "accepted by third"),
    ]

    first_final_response = None
    for keys, validator_id, reason in validators:
        first_final_response = submit_validation_result(
            job_id=job_id,
            validator_id=validator_id,
            approved=True,
            reason=reason,
            signature=_sign_validation_result(keys["private_key"], job_id, validator_id, task_id, True, reason, signed_at),
            signed_at=signed_at,
        )

    with get_connection() as connection:
        original = connection.execute("SELECT * FROM validation_jobs WHERE job_id = ?", (job_id,)).fetchone()
        task = dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone())
        duplicate_block = _accept_block_in_connection(
            connection=connection,
            task=task,
            miner_id=miner_id,
            result_hash=original["result_hash"],
            merkle_root=original["merkle_root"],
            samples=[],
            signature="duplicate-finalization",
            submission_reason="duplicate finalization retry",
            validation_ms=0,
        )

    assert first_final_response["status"] == "approved"
    assert duplicate_block["already_finalized"] is True
    assert duplicate_block["height"] == first_final_response["block"]["height"]
    with get_connection() as connection:
        blocks = connection.execute("SELECT COUNT(*) AS count FROM blocks WHERE task_id = ?", (task_id,)).fetchone()
        rewards = connection.execute("SELECT COUNT(*) AS count FROM rewards WHERE block_height = ?", (first_final_response["block"]["height"],)).fetchone()
        block_rewards = connection.execute(
            "SELECT COUNT(*) AS count FROM ledger_entries WHERE entry_type = 'block_reward' AND related_id = ?",
            (task_id,),
        ).fetchone()
    assert blocks["count"] == 1
    assert rewards["count"] == 1
    assert block_rewards["count"] == 1
    assert get_balance(miner_id)["balance"] == 2.51328


def test_quorum_finalization_failure_rejects_job_without_losing_vote(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "validator-quorum-finalization-failure.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner_id = _register_miner("miner-finalization-failure")
    first_keys = generate_keypair()
    second_keys = generate_keypair()
    third_keys = generate_keypair()
    first_validator = register_validator("validator-one", first_keys["public_key"])
    second_validator = register_validator("validator-two", second_keys["public_key"])
    third_validator = register_validator("validator-three", third_keys["public_key"])
    job_id, task_id = _insert_validation_job(miner_id, first_validator["validator_id"], suffix="failure")

    with get_connection() as connection:
        connection.execute(
            "UPDATE tasks SET tx_merkle_root = ?, tx_count = 1 WHERE task_id = ?",
            ("f" * 64, task_id),
        )

    signed_at = "2026-05-10T00:00:00+00:00"
    validators = [
        (first_keys, first_validator["validator_id"], "accepted by first"),
        (second_keys, second_validator["validator_id"], "accepted by second"),
        (third_keys, third_validator["validator_id"], "accepted by third"),
    ]
    responses = []
    for keys, validator_id, reason in validators:
        signature = _sign_validation_result(
            keys["private_key"],
            job_id,
            validator_id,
            task_id,
            True,
            reason,
            signed_at,
        )
        responses.append(
            submit_validation_result(
                job_id=job_id,
                validator_id=validator_id,
                approved=True,
                reason=reason,
                signature=signature,
                signed_at=signed_at,
            )
        )

    assert responses[-1]["accepted"] is False
    assert responses[-1]["status"] == "rejected"
    assert responses[-1]["approvals"] == 3
    with get_connection() as connection:
        job = connection.execute("SELECT status, result_reason FROM validation_jobs WHERE job_id = ?", (job_id,)).fetchone()
        task = connection.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        votes = connection.execute("SELECT COUNT(*) AS count FROM validation_votes WHERE job_id = ?", (job_id,)).fetchone()
        blocks = connection.execute("SELECT COUNT(*) AS count FROM blocks WHERE task_id = ?", (task_id,)).fetchone()
        rewards = connection.execute("SELECT COUNT(*) AS count FROM rewards WHERE related_id = ?", (task_id,)).fetchone()
        ledger_entries = connection.execute(
            "SELECT COUNT(*) AS count FROM ledger_entries WHERE related_id = ?",
            (task_id,),
        ).fetchone()
        certificates = connection.execute(
            "SELECT COUNT(*) AS count FROM finality_certificates WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    assert job["status"] == "rejected"
    assert "transaction finalization failed" in job["result_reason"]
    assert task["status"] == "rejected"
    assert votes["count"] == 3
    assert blocks["count"] == 0
    assert rewards["count"] == 0
    assert ledger_entries["count"] == 0
    assert certificates["count"] == 0


def test_genesis_balance_and_validator_stake_are_persisted(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "economy.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keys = generate_keypair()
    validator = register_validator("stake-validator", keys["public_key"])
    record_validator_heartbeat(_signed_validator_heartbeat(keys, validator["validator_id"]))
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


def _signed_validator_heartbeat(keys: dict[str, str], validator_id: str) -> dict:
    payload = {
        "validator_id": validator_id,
        "node_id": f"node-{validator_id}",
        "public_key": keys["public_key"],
        "address": "http://127.0.0.1:8000",
        "local_height": 100,
        "effective_height": 100,
        "latest_block_hash": "a" * 64,
        "pending_replay_blocks": 0,
        "sync_lag": 0,
        "version": "0.18",
    }
    payload["signature"] = sign_payload(keys["private_key"], payload)
    return payload


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
                protocol_params_id, selected_tx_hashes, tx_merkle_root, tx_count,
                tx_fee_total_units, selected_tx_hashes_hash, created_at
            )
            VALUES (?, ?, ?, ?, 'bbp_hex_v1', 'revealed', ?, '[]', ?, 0, 0, ?, ?)
            """,
            (
                task_id,
                miner_id,
                range_start,
                range_end,
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
