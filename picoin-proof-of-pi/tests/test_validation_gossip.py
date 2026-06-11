import json
from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.signatures import (
    build_commit_signature_payload,
    build_reveal_signature_payload,
    build_validation_result_signature_payload,
    generate_keypair,
    sign_payload,
)
from app.db.database import get_connection, init_db
from app.services import mining as mining_service
from app.services.mining import (
    MiningError,
    ValidationFinalizationDeferred,
    get_task_status,
    list_validation_job_inventory,
    list_task_inventory,
    receive_validation_job_gossip,
    receive_task_gossip,
    receive_validation_vote_gossip,
    register_miner,
    register_validator,
    reveal_task,
    _accept_block_in_connection,
)
from app.services.transactions import transaction_commitment


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


def test_same_validation_job_finalizes_to_same_block_hash_on_different_nodes(tmp_path, monkeypatch) -> None:
    miner_keys = generate_keypair()
    task_id = "task_shared_candidate"
    job_id = "job_shared_candidate"
    task_created_at = "2026-06-09T16:00:00+00:00"
    job_created_at = "2026-06-09T16:00:07+00:00"
    result_hash = "1" * 64
    merkle_root = "2" * 64
    challenge_seed = "3" * 64

    def finalize(db_name: str) -> dict[str, Any]:
        _use_db(tmp_path, monkeypatch, db_name)
        miner = register_miner("shared-candidate-miner", miner_keys["public_key"])
        tx_commitment = transaction_commitment([])
        with get_connection() as connection:
            params = dict(
                connection.execute("SELECT * FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1").fetchone()
            )
            assignment = mining_service._competitive_round_assignment(connection, params)
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, miner_id, range_start, range_end, algorithm, status,
                    assignment_seed, assignment_mode, competitive_round_height,
                    competitive_round_previous_hash, protocol_params_id, created_at,
                    selected_tx_hashes, tx_merkle_root, tx_count, tx_fee_total_units,
                    selected_tx_hashes_hash
                )
                VALUES (?, ?, ?, ?, ?, 'revealed', ?, ?, ?, ?, ?, ?, '[]', ?, 0, 0, ?)
                """,
                (
                    task_id,
                    miner["miner_id"],
                    assignment["range_start"],
                    assignment["range_end"],
                    params["algorithm"],
                    assignment["assignment_seed"],
                    mining_service.COMPETITIVE_ROUND_ASSIGNMENT_MODE,
                    assignment["round_height"],
                    assignment["previous_hash"],
                    params["id"],
                    task_created_at,
                    tx_commitment["tx_merkle_root"],
                    tx_commitment["selected_tx_hashes_hash"],
                ),
            )
            connection.execute(
                """
                INSERT INTO validation_jobs (
                    job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                    samples, tx_merkle_root, selected_tx_hashes_hash, tx_count,
                    tx_fee_total_units, tx_hashes_json, transactions_json,
                    status, job_created_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?, 0, 0, '[]', '[]', 'pending', ?, ?)
                """,
                (
                    job_id,
                    task_id,
                    miner["miner_id"],
                    result_hash,
                    merkle_root,
                    challenge_seed,
                    tx_commitment["tx_merkle_root"],
                    tx_commitment["selected_tx_hashes_hash"],
                    job_created_at,
                    job_created_at,
                ),
            )
            task = dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone())
            return _accept_block_in_connection(
                connection=connection,
                task=task,
                miner_id=miner["miner_id"],
                result_hash=result_hash,
                merkle_root=merkle_root,
                samples=[],
                signature="validator-quorum",
                submission_reason="validator quorum",
                validation_ms=1,
                params=params,
                validation_job_id=job_id,
            )

    first = finalize("shared-candidate-a.sqlite3")
    second = finalize("shared-candidate-b.sqlite3")

    assert first["timestamp"] == job_created_at
    assert second["timestamp"] == job_created_at
    assert first["total_block_ms"] == 7000
    assert second["total_block_ms"] == 7000
    assert first["block_hash"] == second["block_hash"]


def test_validation_job_finalization_uses_task_compute_ms_for_stable_hash(tmp_path, monkeypatch) -> None:
    miner_keys = generate_keypair()
    task_id = "task_shared_compute_ms"
    job_id = "job_shared_compute_ms"
    job_created_at = "2026-06-11T13:44:35+00:00"
    result_hash = "4" * 64
    merkle_root = "5" * 64
    challenge_seed = "6" * 64
    compute_ms = 87006

    def finalize(db_name: str, task_created_at: str) -> dict[str, Any]:
        _use_db(tmp_path, monkeypatch, db_name)
        miner = register_miner("shared-compute-miner", miner_keys["public_key"])
        tx_commitment = transaction_commitment([])
        with get_connection() as connection:
            params = dict(
                connection.execute("SELECT * FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1").fetchone()
            )
            assignment = mining_service._competitive_round_assignment(connection, params)
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, miner_id, range_start, range_end, algorithm, status,
                    assignment_seed, assignment_mode, competitive_round_height,
                    competitive_round_previous_hash, protocol_params_id, created_at,
                    compute_ms, selected_tx_hashes, tx_merkle_root, tx_count,
                    tx_fee_total_units, selected_tx_hashes_hash
                )
                VALUES (?, ?, ?, ?, ?, 'revealed', ?, ?, ?, ?, ?, ?, ?, '[]', ?, 0, 0, ?)
                """,
                (
                    task_id,
                    miner["miner_id"],
                    assignment["range_start"],
                    assignment["range_end"],
                    params["algorithm"],
                    assignment["assignment_seed"],
                    mining_service.COMPETITIVE_ROUND_ASSIGNMENT_MODE,
                    assignment["round_height"],
                    assignment["previous_hash"],
                    params["id"],
                    task_created_at,
                    compute_ms,
                    tx_commitment["tx_merkle_root"],
                    tx_commitment["selected_tx_hashes_hash"],
                ),
            )
            connection.execute(
                """
                INSERT INTO validation_jobs (
                    job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                    samples, tx_merkle_root, selected_tx_hashes_hash, tx_count,
                    tx_fee_total_units, tx_hashes_json, transactions_json,
                    status, job_created_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?, 0, 0, '[]', '[]', 'pending', ?, ?)
                """,
                (
                    job_id,
                    task_id,
                    miner["miner_id"],
                    result_hash,
                    merkle_root,
                    challenge_seed,
                    tx_commitment["tx_merkle_root"],
                    tx_commitment["selected_tx_hashes_hash"],
                    job_created_at,
                    job_created_at,
                ),
            )
            task = dict(connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone())
            return _accept_block_in_connection(
                connection=connection,
                task=task,
                miner_id=miner["miner_id"],
                result_hash=result_hash,
                merkle_root=merkle_root,
                samples=[],
                signature="validator-quorum",
                submission_reason="validator quorum",
                validation_ms=1,
                params=params,
                validation_job_id=job_id,
            )

    first = finalize("shared-compute-a.sqlite3", "2026-06-11T13:00:00+00:00")
    second = finalize("shared-compute-b.sqlite3", "2026-06-10T02:48:48+00:00")

    assert first["timestamp"] == job_created_at
    assert second["timestamp"] == job_created_at
    assert first["total_block_ms"] == compute_ms
    assert second["total_block_ms"] == compute_ms
    assert first["block_hash"] == second["block_hash"]


def test_validation_finalization_defers_when_competitive_parent_is_not_tip(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "defer-parent-mismatch.sqlite3")
    miner = register_miner("defer-miner", generate_keypair()["public_key"])
    now = "2026-06-11T14:00:00+00:00"
    local_tip_hash = "b" * 64
    expected_parent_hash = "a" * 64
    tx_commitment = transaction_commitment([])

    with get_connection() as connection:
        params = dict(
            connection.execute("SELECT * FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1").fetchone()
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at
            )
            VALUES ('task_local_parent', ?, 1, 8, 'bbp_hex_v1', 'accepted', ?, ?)
            """,
            (miner["miner_id"], params["id"], now),
        )
        connection.execute(
            """
            INSERT INTO blocks (
                height, previous_hash, miner_id, range_start, range_end, algorithm,
                result_hash, merkle_root, samples, timestamp, block_hash, reward,
                reward_units, tx_merkle_root, tx_count, tx_hashes, fee_reward,
                fee_reward_units, difficulty, task_id, protocol_params_id,
                protocol_version, validation_mode
            )
            VALUES (1, ?, ?, 1, 8, 'bbp_hex_v1', ?, ?, '[]', ?, ?, 0,
                    0, '', 0, '[]', 0, 0, 0.03125, 'task_local_parent', ?,
                    ?, ?)
            """,
            (
                "0" * 64,
                miner["miner_id"],
                "c" * 64,
                "d" * 64,
                now,
                local_tip_hash,
                params["id"],
                params["protocol_version"],
                params["validation_mode"],
            ),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                assignment_seed, assignment_mode, competitive_round_height,
                competitive_round_previous_hash, protocol_params_id, created_at,
                compute_ms, selected_tx_hashes, tx_merkle_root, tx_count,
                tx_fee_total_units, selected_tx_hashes_hash
            )
            VALUES ('task_deferred_parent', ?, 10, 17, 'bbp_hex_v1', 'revealed',
                    ?, ?, 2, ?, ?, ?, 1000, '[]', ?, 0, 0, ?)
            """,
            (
                miner["miner_id"],
                "seed_deferred_parent",
                mining_service.COMPETITIVE_ROUND_ASSIGNMENT_MODE,
                expected_parent_hash,
                params["id"],
                now,
                tx_commitment["tx_merkle_root"],
                tx_commitment["selected_tx_hashes_hash"],
            ),
        )
        task = dict(connection.execute("SELECT * FROM tasks WHERE task_id = 'task_deferred_parent'").fetchone())

        with pytest.raises(ValidationFinalizationDeferred):
            _accept_block_in_connection(
                connection=connection,
                task=task,
                miner_id=miner["miner_id"],
                result_hash="e" * 64,
                merkle_root="f" * 64,
                samples=[],
                signature="validator-quorum",
                submission_reason="validator quorum",
                validation_ms=1,
                params=params,
                validation_job_id=None,
            )

        inserted = connection.execute("SELECT 1 FROM blocks WHERE task_id = 'task_deferred_parent'").fetchone()
        assert inserted is None


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


def test_validation_job_gossip_promotes_existing_expired_task_to_revealed(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "source-existing-expired-task-job.sqlite3")
    ids = _insert_revealed_validation_job()
    envelope = list_validation_job_inventory()["jobs"][0]

    _use_db(tmp_path, monkeypatch, "target-existing-expired-task-job.sqlite3")
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO miners (miner_id, name, public_key, registered_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                ids["miner_id"],
                ids["miner_id"],
                generate_keypair()["public_key"],
                "2026-06-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, expires_at
            )
            VALUES (?, ?, 1000, 1063, 'bbp_hex_v1', 'expired', ?, ?, ?)
            """,
            (
                ids["task_id"],
                ids["miner_id"],
                protocol_params_id,
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T00:10:00+00:00",
            ),
        )

    result = receive_validation_job_gossip(envelope, source_peer="http://node-a")

    assert result["status"] == "accepted"
    with get_connection() as connection:
        task = connection.execute("SELECT status, expires_at FROM tasks WHERE task_id = ?", (ids["task_id"],)).fetchone()
        job = connection.execute("SELECT status FROM validation_jobs WHERE job_id = ?", (ids["job_id"],)).fetchone()
    assert task["status"] == "revealed"
    assert task["expires_at"] != "2026-06-01T00:10:00+00:00"
    assert job is not None
    assert job["status"] == "pending"


def test_duplicate_validation_job_gossip_promotes_existing_expired_task_to_revealed(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "source-duplicate-expired-task-job.sqlite3")
    ids = _insert_revealed_validation_job()
    envelope = list_validation_job_inventory()["jobs"][0]

    _use_db(tmp_path, monkeypatch, "target-duplicate-expired-task-job.sqlite3")
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO miners (miner_id, name, public_key, registered_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                ids["miner_id"],
                ids["miner_id"],
                generate_keypair()["public_key"],
                "2026-06-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, expires_at
            )
            VALUES (?, ?, 1000, 1063, 'bbp_hex_v1', 'expired', ?, ?, ?)
            """,
            (
                ids["task_id"],
                ids["miner_id"],
                protocol_params_id,
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T00:10:00+00:00",
            ),
        )

    first = receive_validation_job_gossip(envelope, source_peer="http://node-a")
    with get_connection() as connection:
        connection.execute(
            "UPDATE tasks SET status = 'expired', expires_at = ? WHERE task_id = ?",
            ("2026-06-01T00:10:00+00:00", ids["task_id"]),
        )
    duplicate = receive_validation_job_gossip(envelope, source_peer="http://node-a")

    assert first["status"] == "accepted"
    assert duplicate["status"] == "duplicate"
    with get_connection() as connection:
        task = connection.execute("SELECT status, expires_at FROM tasks WHERE task_id = ?", (ids["task_id"],)).fetchone()
    assert task["status"] == "revealed"
    assert task["expires_at"] != "2026-06-01T00:10:00+00:00"


def test_task_gossip_imports_commitment_and_snapshot_for_reveal(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "source-task.sqlite3")
    miner_keys = generate_keypair()
    miner = register_miner("task-gossip-miner", miner_keys["public_key"])
    task_id = "task_state_gossip"
    now = datetime.now(timezone.utc).isoformat()
    tx_commitment = transaction_commitment([])
    snapshot_id = "snapshot_task_state_gossip"
    result_hash = "d" * 64
    merkle_root = "e" * 64
    challenge_seed = "f" * 64
    signed_at = now
    commit_signature = sign_payload(
        miner_keys["private_key"],
        build_commit_signature_payload(
            task_id=task_id,
            miner_id=miner["miner_id"],
            range_start=2000,
            range_end=2007,
            algorithm="bbp_hex_v1",
            result_hash=result_hash,
            merkle_root=merkle_root,
            signed_at=signed_at,
            tx_merkle_root=tx_commitment["tx_merkle_root"],
            mempool_snapshot_id=snapshot_id,
            selected_tx_hashes_hash=tx_commitment["selected_tx_hashes_hash"],
            tx_count=tx_commitment["tx_count"],
            tx_fee_total_units=tx_commitment["tx_fee_total_units"],
            chain_id=mining_service.CHAIN_ID,
            network_id=mining_service.NETWORK_ID,
        ),
    )
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, mempool_snapshot_id,
                selected_tx_hashes, tx_merkle_root, tx_count,
                tx_fee_total_units, selected_tx_hashes_hash
            )
            VALUES (?, ?, 2000, 2007, 'bbp_hex_v1', 'committed', ?, ?, ?, '[]', ?, 0, 0, ?)
            """,
            (task_id, miner["miner_id"], protocol_params_id, now, snapshot_id, tx_commitment["tx_merkle_root"], tx_commitment["selected_tx_hashes_hash"]),
        )
        connection.execute(
            """
            INSERT INTO task_tx_snapshots (
                snapshot_id, task_id, block_height, tx_hashes_json, tx_merkle_root,
                tx_count, tx_fee_total_units, created_at
            )
            VALUES (?, ?, 1, '[]', ?, 0, 0, ?)
            """,
            (snapshot_id, task_id, tx_commitment["tx_merkle_root"], now),
        )
        connection.execute(
            """
            INSERT INTO commitments (
                task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, tx_merkle_root, mempool_snapshot_id, selected_tx_hashes_hash,
                tx_count, tx_fee_total_units, signature, signed_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, 0, 0, ?, ?, ?)
            """,
            (
                task_id,
                miner["miner_id"],
                result_hash,
                merkle_root,
                challenge_seed,
                tx_commitment["tx_merkle_root"],
                snapshot_id,
                tx_commitment["selected_tx_hashes_hash"],
                commit_signature,
                signed_at,
                now,
            ),
        )
    envelope = list_task_inventory(status="committed")["tasks"][0]

    _use_db(tmp_path, monkeypatch, "target-task.sqlite3")
    result = receive_task_gossip(envelope, source_peer="http://node-a")
    duplicate = receive_task_gossip(envelope, source_peer="http://node-a")

    assert result["status"] == "accepted"
    assert duplicate["status"] == "duplicate"
    reveal_signed_at = datetime.now(timezone.utc).isoformat()
    reveal_signature = sign_payload(
        miner_keys["private_key"],
        build_reveal_signature_payload(
            task_id=task_id,
            miner_id=miner["miner_id"],
            merkle_root=merkle_root,
            challenge_seed=challenge_seed,
            signed_at=reveal_signed_at,
            tx_merkle_root=tx_commitment["tx_merkle_root"],
            mempool_snapshot_id=snapshot_id,
            selected_tx_hashes_hash=tx_commitment["selected_tx_hashes_hash"],
        ),
    )
    reveal = reveal_task(
        task_id=task_id,
        miner_id=miner["miner_id"],
        revealed_samples=[],
        signature=reveal_signature,
        signed_at=reveal_signed_at,
        tx_merkle_root=tx_commitment["tx_merkle_root"],
        mempool_snapshot_id=snapshot_id,
        selected_tx_hashes_hash=tx_commitment["selected_tx_hashes_hash"],
    )

    assert reveal["accepted"] is True
    assert reveal["status"] == "validation_pending"
    with get_connection() as connection:
        task = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        commitment = connection.execute("SELECT * FROM commitments WHERE task_id = ?", (task_id,)).fetchone()
        snapshot = connection.execute("SELECT * FROM task_tx_snapshots WHERE task_id = ?", (task_id,)).fetchone()
        job = connection.execute("SELECT * FROM validation_jobs WHERE task_id = ?", (task_id,)).fetchone()
    assert task is not None and task["status"] == "revealed"
    assert commitment is not None
    assert snapshot is not None
    assert job is not None and job["status"] == "pending"


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


def test_vote_gossip_marks_already_accepted_block_job_approved(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "already-accepted-vote.sqlite3")
    monkeypatch.setattr(mining_service, "_effective_required_validator_approvals", lambda connection, params: 1)

    miner = register_miner("accepted-block-miner", generate_keypair()["public_key"])
    validator_keys = generate_keypair()
    validator = register_validator("accepted-block-validator", validator_keys["public_key"])
    now = datetime.now(timezone.utc).isoformat()
    task_id = "task_already_accepted"
    job_id = "job_already_accepted"
    result_hash = "1" * 64
    merkle_root = "2" * 64
    block_hash = "3" * 64
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, submitted_at
            )
            VALUES (?, ?, 3000, 3007, 'bbp_hex_v1', 'accepted', ?, ?, ?)
            """,
            (task_id, miner["miner_id"], protocol_params_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO blocks (
                height, previous_hash, miner_id, range_start, range_end, algorithm,
                result_hash, merkle_root, samples, timestamp, block_hash, reward,
                reward_units, tx_merkle_root, tx_count, tx_hashes, task_id,
                protocol_params_id, protocol_version, validation_mode
            )
            VALUES (
                1, ?, ?, 3000, 3007, 'bbp_hex_v1', ?, ?, '[]', ?, ?, 0,
                0, ?, 0, '[]', ?, ?, '1.0', 'external_commit_reveal'
            )
            """,
            ("0" * 64, miner["miner_id"], result_hash, merkle_root, now, block_hash, "4" * 64, task_id, protocol_params_id),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at, job_created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '[]', 'pending', ?, ?)
            """,
            (job_id, task_id, miner["miner_id"], result_hash, merkle_root, "5" * 64, now, now),
        )

    signed_at = datetime.now(timezone.utc).isoformat()
    reason = "external validator accepted samples"
    signature = sign_payload(
        validator_keys["private_key"],
        build_validation_result_signature_payload(
            job_id=job_id,
            validator_id=validator["validator_id"],
            task_id=task_id,
            approved=True,
            reason=reason,
            signed_at=signed_at,
        ),
    )
    result = receive_validation_vote_gossip(
        {
            "vote": {
                "job_id": job_id,
                "task_id": task_id,
                "validator_id": validator["validator_id"],
                "approved": True,
                "reason": reason,
                "signature": signature,
                "signed_at": signed_at,
                "validation_ms": 3,
                "created_at": signed_at,
            }
        },
        source_peer="http://node-b",
    )

    assert result["status"] == "accepted"
    assert result["finalization"]["status"] == "approved"
    with get_connection() as connection:
        task = connection.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        job = connection.execute("SELECT status, result_reason FROM validation_jobs WHERE job_id = ?", (job_id,)).fetchone()
    assert task["status"] == "accepted"
    assert job["status"] == "approved"
    assert "already accepted" in job["result_reason"]
    status = get_task_status(task_id)
    assert status["status"] == "accepted"
    assert status["validation"]["status"] == "approved"


def test_task_status_prefers_finality_certificate_over_rejected_job(tmp_path, monkeypatch) -> None:
    _use_db(tmp_path, monkeypatch, "finality-status.sqlite3")

    miner = register_miner("finality-status-miner", generate_keypair()["public_key"])
    now = datetime.now(timezone.utc).isoformat()
    task_id = "task_finality_status"
    job_id = "job_finality_status"
    result_hash = "6" * 64
    merkle_root = "7" * 64
    block_hash = "8" * 64
    certificate_hash = "9" * 64
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, submitted_at
            )
            VALUES (?, ?, 4000, 4007, 'bbp_hex_v1', 'accepted', ?, ?, ?)
            """,
            (task_id, miner["miner_id"], protocol_params_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, result_reason, created_at, job_created_at,
                completed_at, finalized_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '[]', 'rejected', 'competitive round closed', ?, ?, ?, ?)
            """,
            (job_id, task_id, miner["miner_id"], result_hash, merkle_root, "a" * 64, now, now, now, now),
        )
        connection.execute(
            """
            INSERT INTO blocks (
                height, previous_hash, miner_id, range_start, range_end, algorithm,
                result_hash, merkle_root, samples, timestamp, block_hash, reward,
                reward_units, tx_merkle_root, tx_count, tx_hashes, task_id,
                protocol_params_id, protocol_version, validation_mode
            )
            VALUES (
                1, ?, ?, 4000, 4007, 'bbp_hex_v1', ?, ?, '[]', ?, ?, 0,
                0, ?, 0, '[]', ?, ?, '1.0', 'external_commit_reveal'
            )
            """,
            ("0" * 64, miner["miner_id"], result_hash, merkle_root, now, block_hash, "b" * 64, task_id, protocol_params_id),
        )
        payload = {
            "version": "picoin-finality-v1",
            "network_id": mining_service.NETWORK_ID,
            "chain_id": mining_service.CHAIN_ID,
            "block": {"height": 1, "block_hash": block_hash, "task_id": task_id},
            "validation": {"job_id": job_id, "required_approvals": 3, "approval_count": 3},
        }
        connection.execute(
            """
            INSERT INTO finality_certificates (
                block_height, block_hash, task_id, job_id, miner_id, network_id, chain_id,
                protocol_version, protocol_params_id, required_approvals, approval_count,
                certificate_hash, payload_json, votes_json, created_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, '1.0', ?, 3, 3, ?, ?, '[]', ?)
            """,
            (
                block_hash,
                task_id,
                job_id,
                miner["miner_id"],
                mining_service.NETWORK_ID,
                str(mining_service.CHAIN_ID),
                protocol_params_id,
                certificate_hash,
                json.dumps(payload),
                now,
            ),
        )

    status = get_task_status(task_id)

    assert status["status"] == "accepted"
    assert status["validation"]["status"] == "approved"
    assert status["validation"]["job_id"] == job_id
    assert status["validation"]["approvals"] == 3
    assert status["validation"]["required_approvals"] == 3
