import sqlite3

import pytest

from app.core.crypto import hash_result
from app.core.merkle import merkle_root
from app.core.pi import calculate_pi_segment
from app.core.signatures import build_commit_signature_payload, build_submission_signature_payload, generate_keypair, sign_payload
from app.db.database import _ensure_tasks_range_constraints
from app.db.database import get_connection
from app.db.database import init_db
from app.services import mining as mining_service
from app.services.mining import (
    MiningError,
    cleanup_expired_tasks,
    create_next_task,
    get_full_economic_audit,
    get_validation_job,
    miner_id_from_public_key,
    record_validator_heartbeat,
    register_miner,
    commit_task,
    submit_task,
)
from app.services.wallet import create_wallet


def _heartbeat_validator(
    keys: dict[str, str],
    validator_id: str,
    node_id: str = "assignment-node",
    name: str | None = None,
) -> None:
    payload = {
        "validator_id": validator_id,
        "name": name or validator_id,
        "node_id": node_id,
        "public_key": keys["public_key"],
        "address": f"http://{node_id}:8000",
        "local_height": 100,
        "effective_height": 100,
        "latest_block_hash": "a" * 64,
        "pending_replay_blocks": 0,
        "sync_lag": 0,
        "version": "0.18",
    }
    payload["signature"] = sign_payload(keys["private_key"], payload)
    record_validator_heartbeat(payload)


def _submit_legacy_task(task: dict, miner_id: str, private_key: str) -> dict:
    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    signed_at = "2026-05-18T00:00:00+00:00"
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
    return submit_task(task["task_id"], miner_id, result_hash, segment, signature, signed_at)


def test_tasks_range_constraint_migration_adds_competitive_columns_first(tmp_path) -> None:
    db_path = tmp_path / "legacy-tasks-constraints.sqlite3"
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                miner_id TEXT NOT NULL,
                range_start INTEGER NOT NULL,
                range_end INTEGER NOT NULL,
                algorithm TEXT NOT NULL,
                status TEXT NOT NULL,
                assignment_seed TEXT,
                assignment_mode TEXT,
                assignment_ms INTEGER,
                compute_ms INTEGER,
                protocol_params_id INTEGER,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                submitted_at TEXT,
                mempool_snapshot_id TEXT,
                selected_tx_hashes TEXT NOT NULL DEFAULT '[]',
                tx_merkle_root TEXT NOT NULL DEFAULT '',
                tx_count INTEGER NOT NULL DEFAULT 0,
                tx_fee_total_units INTEGER NOT NULL DEFAULT 0,
                selected_tx_hashes_hash TEXT
            );
            """
        )

        _ensure_tasks_range_constraints(connection)

        columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(tasks)").fetchall()}
    finally:
        connection.close()

    assert "competitive_round_height" in columns
    assert "competitive_round_previous_hash" in columns
    assert "idx_tasks_competitive_height" in indexes


def test_pseudo_random_assignment_returns_non_sequential_ranges(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "assigned")
    init_db(db_path)

    first_keys = generate_keypair()
    second_keys = generate_keypair()
    first_miner = register_miner("assignment-a", first_keys["public_key"])
    second_miner = register_miner("assignment-b", second_keys["public_key"])

    first_task = create_next_task(first_miner["miner_id"])
    second_task = create_next_task(second_miner["miner_id"])

    assert first_task["assignment_mode"] == "pseudo_random"
    assert first_task["assignment_seed"]
    assert second_task["assignment_seed"]

    overlaps = first_task["range_start"] <= second_task["range_end"] and first_task["range_end"] >= second_task["range_start"]
    assert overlaps is False


def test_competitive_round_assignment_gives_miners_same_round_range(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-competitive-round.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    init_db(db_path)

    first_keys = generate_keypair()
    second_keys = generate_keypair()
    first_miner = register_miner("competitive-a", first_keys["public_key"])
    second_miner = register_miner("competitive-b", second_keys["public_key"])

    first_task = create_next_task(first_miner["miner_id"])
    second_task = create_next_task(second_miner["miner_id"])
    repeated_first_task = create_next_task(first_miner["miner_id"])

    assert first_task["task_id"] != second_task["task_id"]
    assert first_task["task_id"] == repeated_first_task["task_id"]
    assert first_task["assignment_mode"] == "competitive_round"
    assert second_task["assignment_mode"] == "competitive_round"
    assert first_task["assignment_seed"] == second_task["assignment_seed"]
    assert first_task["range_start"] == second_task["range_start"]
    assert first_task["range_end"] == second_task["range_end"]
    assert first_task["competitive_round_height"] == 1
    assert second_task["competitive_round_height"] == 1
    assert first_task["competitive_round_previous_hash"] == second_task["competitive_round_previous_hash"]


def test_competitive_round_task_insert_collision_returns_existing_task(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-competitive-round-insert-collision.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    init_db(db_path)

    import app.db.database as database_module

    keys = generate_keypair()
    miner = register_miner("competitive-insert-race", keys["public_key"])
    original_execute = database_module.PicoinConnection.execute
    triggered = {"value": False}

    def execute_with_task_insert_collision(self, sql, parameters=(), /):
        cursor = original_execute(self, sql, parameters)
        if not triggered["value"] and "INSERT INTO tasks" in sql:
            triggered["value"] = True
            raise sqlite3.IntegrityError("UNIQUE constraint failed: tasks.task_id")
        return cursor

    monkeypatch.setattr(database_module.PicoinConnection, "execute", execute_with_task_insert_collision)

    task = create_next_task(miner["miner_id"])

    assert triggered["value"] is True
    assert task["miner_id"] == miner["miner_id"]
    assert task["status"] == "assigned"
    with get_connection() as connection:
        task_count = connection.execute(
            "SELECT COUNT(*) AS count FROM tasks WHERE miner_id = ?",
            (miner["miner_id"],),
        ).fetchone()["count"]
    assert task_count == 1


def test_register_miner_reuses_portable_identity_for_public_key(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-portable-miner.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    first_reward = create_wallet("portable-first-reward")
    second_reward = create_wallet("portable-second-reward")

    first = register_miner("portable-a", keypair["public_key"], first_reward["address"])
    second = register_miner("portable-b", keypair["public_key"], second_reward["address"])

    expected_miner_id = miner_id_from_public_key(keypair["public_key"])
    assert first["miner_id"] == expected_miner_id
    assert second["miner_id"] == expected_miner_id
    assert second["name"] == "portable-b"
    assert second["reward_address"] == second_reward["address"]
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT miner_id, public_key FROM miners WHERE public_key = ?",
            (keypair["public_key"],),
        ).fetchall()
    assert [row["miner_id"] for row in rows] == [expected_miner_id]


def test_task_assignment_restores_deterministic_miner_identity_on_peer(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-deterministic-miner-restore.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    init_db(db_path)

    keypair = generate_keypair()
    miner_id = miner_id_from_public_key(keypair["public_key"])
    task = create_next_task(
        miner_id,
        public_key=keypair["public_key"],
        name="portable-peer-miner",
    )

    assert task is not None
    assert task["miner_id"] == miner_id
    assert task["status"] == "assigned"
    with get_connection() as connection:
        miner = connection.execute(
            "SELECT miner_id, name, public_key FROM miners WHERE miner_id = ?",
            (miner_id,),
        ).fetchone()
    assert miner["name"] == "portable-peer-miner"
    assert miner["public_key"] == keypair["public_key"]


def test_task_response_network_context_signs_commit_without_local_defaults(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-task-network-context.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("network-context-miner", keypair["public_key"])
    task = create_next_task(miner["miner_id"])

    assert task["network_id"] == mining_service.NETWORK_ID
    assert task["chain_id"] == mining_service.CHAIN_ID

    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    root = merkle_root(segment, task["range_start"])
    signed_at = "2026-06-07T20:45:00+00:00"
    signature_payload = build_commit_signature_payload(
        task_id=task["task_id"],
        miner_id=miner["miner_id"],
        range_start=task["range_start"],
        range_end=task["range_end"],
        algorithm=task["algorithm"],
        result_hash=result_hash,
        merkle_root=root,
        signed_at=signed_at,
        tx_merkle_root=task.get("tx_merkle_root", ""),
        mempool_snapshot_id=task.get("mempool_snapshot_id"),
        selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
        tx_count=int(task.get("tx_count") or 0),
        tx_fee_total_units=int(task.get("tx_fee_total_units") or 0),
        chain_id=task.get("chain_id") or "wrong-client-chain",
        network_id=task.get("network_id") or "wrong-client-network",
    )
    signature = sign_payload(keypair["private_key"], signature_payload)

    response = commit_task(
        task_id=task["task_id"],
        miner_id=miner["miner_id"],
        result_hash=result_hash,
        merkle_root=root,
        tx_merkle_root=task.get("tx_merkle_root", ""),
        mempool_snapshot_id=task.get("mempool_snapshot_id"),
        selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
        tx_count=int(task.get("tx_count") or 0),
        tx_fee_total_units=int(task.get("tx_fee_total_units") or 0),
        compute_ms=1,
        signature=signature,
        signed_at=signed_at,
    )

    assert response["accepted"] is True
    assert response["status"] == "committed"


def test_committed_task_can_resume_through_next_task_response(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-committed-resume.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("resume-committed-miner", keypair["public_key"])
    task = create_next_task(miner["miner_id"])

    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    root = merkle_root(segment, task["range_start"])

    def signed_commit(signed_at: str, *, result: str = result_hash, merkle: str = root) -> str:
        return sign_payload(
            keypair["private_key"],
            build_commit_signature_payload(
                task_id=task["task_id"],
                miner_id=miner["miner_id"],
                range_start=task["range_start"],
                range_end=task["range_end"],
                algorithm=task["algorithm"],
                result_hash=result,
                merkle_root=merkle,
                signed_at=signed_at,
                tx_merkle_root=task.get("tx_merkle_root", ""),
                mempool_snapshot_id=task.get("mempool_snapshot_id"),
                selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
                tx_count=int(task.get("tx_count") or 0),
                tx_fee_total_units=int(task.get("tx_fee_total_units") or 0),
                chain_id=task.get("chain_id") or mining_service.CHAIN_ID,
                network_id=task.get("network_id") or mining_service.NETWORK_ID,
            ),
        )

    first = commit_task(
        task_id=task["task_id"],
        miner_id=miner["miner_id"],
        result_hash=result_hash,
        merkle_root=root,
        tx_merkle_root=task.get("tx_merkle_root", ""),
        mempool_snapshot_id=task.get("mempool_snapshot_id"),
        selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
        tx_count=int(task.get("tx_count") or 0),
        tx_fee_total_units=int(task.get("tx_fee_total_units") or 0),
        compute_ms=1,
        signature=signed_commit("2026-06-08T19:30:00+00:00"),
        signed_at="2026-06-08T19:30:00+00:00",
    )
    assert first["accepted"] is True

    resumed = create_next_task(miner["miner_id"])

    assert resumed["task_id"] == task["task_id"]
    assert resumed["status"] == "assigned"
    assert resumed["resume_status"] == "committed"

    second = commit_task(
        task_id=task["task_id"],
        miner_id=miner["miner_id"],
        result_hash=result_hash,
        merkle_root=root,
        tx_merkle_root=resumed.get("tx_merkle_root", ""),
        mempool_snapshot_id=resumed.get("mempool_snapshot_id"),
        selected_tx_hashes_hash=resumed.get("selected_tx_hashes_hash"),
        tx_count=int(resumed.get("tx_count") or 0),
        tx_fee_total_units=int(resumed.get("tx_fee_total_units") or 0),
        compute_ms=1,
        signature=signed_commit("2026-06-08T19:31:00+00:00"),
        signed_at="2026-06-08T19:31:00+00:00",
    )

    assert second["accepted"] is True
    assert second["status"] == "committed"
    assert second["challenge_seed"] == first["challenge_seed"]
    assert second["samples"] == first["samples"]


def test_expired_committed_competitive_task_resumes_reveal(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-expired-committed-resume.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("resume-expired-committed-miner", keypair["public_key"])
    task = create_next_task(miner["miner_id"])
    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    root = merkle_root(segment, task["range_start"])
    signed_at = "2026-06-08T19:30:00+00:00"
    signature = sign_payload(
        keypair["private_key"],
        build_commit_signature_payload(
            task_id=task["task_id"],
            miner_id=miner["miner_id"],
            range_start=task["range_start"],
            range_end=task["range_end"],
            algorithm=task["algorithm"],
            result_hash=result_hash,
            merkle_root=root,
            signed_at=signed_at,
            tx_merkle_root=task.get("tx_merkle_root", ""),
            mempool_snapshot_id=task.get("mempool_snapshot_id"),
            selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
            tx_count=int(task.get("tx_count") or 0),
            tx_fee_total_units=int(task.get("tx_fee_total_units") or 0),
            chain_id=task.get("chain_id") or mining_service.CHAIN_ID,
            network_id=task.get("network_id") or mining_service.NETWORK_ID,
        ),
    )
    first = commit_task(
        task_id=task["task_id"],
        miner_id=miner["miner_id"],
        result_hash=result_hash,
        merkle_root=root,
        tx_merkle_root=task.get("tx_merkle_root", ""),
        mempool_snapshot_id=task.get("mempool_snapshot_id"),
        selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
        tx_count=int(task.get("tx_count") or 0),
        tx_fee_total_units=int(task.get("tx_fee_total_units") or 0),
        compute_ms=1,
        signature=signature,
        signed_at=signed_at,
    )
    assert first["accepted"] is True

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET status = 'expired',
                expires_at = '2026-06-01T00:00:00+00:00'
            WHERE task_id = ?
            """,
            (task["task_id"],),
        )

    resumed = create_next_task(miner["miner_id"])

    assert resumed["task_id"] == task["task_id"]
    assert resumed["status"] == "assigned"
    assert resumed["resume_status"] == "committed"
    assert resumed["expires_at"] != "2026-06-01T00:00:00+00:00"
    with get_connection() as connection:
        row = connection.execute("SELECT status FROM tasks WHERE task_id = ?", (task["task_id"],)).fetchone()
        job_count = connection.execute(
            "SELECT COUNT(*) AS count FROM validation_jobs WHERE task_id = ?",
            (task["task_id"],),
        ).fetchone()["count"]
    assert row["status"] == "committed"
    assert job_count == 0


def test_expired_committed_competitive_task_resumes_before_recomputing_assignment(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-expired-committed-resume-before-assignment.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("resume-before-assignment-miner", keypair["public_key"])
    task = create_next_task(miner["miner_id"])
    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    root = merkle_root(segment, task["range_start"])
    signed_at = "2026-06-08T19:30:00+00:00"
    signature = sign_payload(
        keypair["private_key"],
        build_commit_signature_payload(
            task_id=task["task_id"],
            miner_id=miner["miner_id"],
            range_start=task["range_start"],
            range_end=task["range_end"],
            algorithm=task["algorithm"],
            result_hash=result_hash,
            merkle_root=root,
            signed_at=signed_at,
            tx_merkle_root=task.get("tx_merkle_root", ""),
            mempool_snapshot_id=task.get("mempool_snapshot_id"),
            selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
            tx_count=int(task.get("tx_count") or 0),
            tx_fee_total_units=int(task.get("tx_fee_total_units") or 0),
            chain_id=task.get("chain_id") or mining_service.CHAIN_ID,
            network_id=task.get("network_id") or mining_service.NETWORK_ID,
        ),
    )
    first = commit_task(
        task_id=task["task_id"],
        miner_id=miner["miner_id"],
        result_hash=result_hash,
        merkle_root=root,
        tx_merkle_root=task.get("tx_merkle_root", ""),
        mempool_snapshot_id=task.get("mempool_snapshot_id"),
        selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
        tx_count=int(task.get("tx_count") or 0),
        tx_fee_total_units=int(task.get("tx_fee_total_units") or 0),
        compute_ms=1,
        signature=signature,
        signed_at=signed_at,
    )
    assert first["accepted"] is True

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET status = 'expired',
                expires_at = '2026-06-01T00:00:00+00:00'
            WHERE task_id = ?
            """,
            (task["task_id"],),
        )

    def fail_assignment(connection, params):
        raise AssertionError("expired committed task should resume before assignment is recomputed")

    monkeypatch.setattr(mining_service, "_competitive_round_assignment", fail_assignment)

    resumed = create_next_task(miner["miner_id"])

    assert resumed["task_id"] == task["task_id"]
    assert resumed["status"] == "assigned"
    assert resumed["resume_status"] == "committed"


def test_committed_task_retry_rejects_mismatched_commitment(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-committed-mismatch.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("resume-mismatch-miner", keypair["public_key"])
    task = create_next_task(miner["miner_id"])
    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    root = merkle_root(segment, task["range_start"])

    signed_at = "2026-06-08T19:32:00+00:00"
    signature = sign_payload(
        keypair["private_key"],
        build_commit_signature_payload(
            task_id=task["task_id"],
            miner_id=miner["miner_id"],
            range_start=task["range_start"],
            range_end=task["range_end"],
            algorithm=task["algorithm"],
            result_hash=result_hash,
            merkle_root=root,
            signed_at=signed_at,
            tx_merkle_root=task.get("tx_merkle_root", ""),
            mempool_snapshot_id=task.get("mempool_snapshot_id"),
            selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
            tx_count=int(task.get("tx_count") or 0),
            tx_fee_total_units=int(task.get("tx_fee_total_units") or 0),
            chain_id=task.get("chain_id") or mining_service.CHAIN_ID,
            network_id=task.get("network_id") or mining_service.NETWORK_ID,
        ),
    )
    first = commit_task(
        task_id=task["task_id"],
        miner_id=miner["miner_id"],
        result_hash=result_hash,
        merkle_root=root,
        tx_merkle_root=task.get("tx_merkle_root", ""),
        mempool_snapshot_id=task.get("mempool_snapshot_id"),
        selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
        tx_count=int(task.get("tx_count") or 0),
        tx_fee_total_units=int(task.get("tx_fee_total_units") or 0),
        compute_ms=1,
        signature=signature,
        signed_at=signed_at,
    )
    assert first["accepted"] is True

    retry = commit_task(
        task_id=task["task_id"],
        miner_id=miner["miner_id"],
        result_hash="f" * 64,
        merkle_root=root,
        tx_merkle_root=task.get("tx_merkle_root", ""),
        mempool_snapshot_id=task.get("mempool_snapshot_id"),
        selected_tx_hashes_hash=task.get("selected_tx_hashes_hash"),
        tx_count=int(task.get("tx_count") or 0),
        tx_fee_total_units=int(task.get("tx_fee_total_units") or 0),
        compute_ms=1,
        signature=signature,
        signed_at=signed_at,
    )

    assert retry["accepted"] is False
    assert retry["message"] == "commitment mismatch"


def test_competitive_task_id_is_portable_across_nodes(tmp_path, monkeypatch) -> None:
    keypair = generate_keypair()
    tasks = []

    for node_name in ("a", "b"):
        db_path = tmp_path / f"assignment-portable-task-{node_name}.sqlite3"
        monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
        monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
        monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
        init_db(db_path)

        miner = register_miner("portable-task-miner", keypair["public_key"])
        tasks.append(create_next_task(miner["miner_id"]))

    assert tasks[0]["task_id"] == tasks[1]["task_id"]
    assert tasks[0]["miner_id"] == tasks[1]["miner_id"]
    assert tasks[0]["assignment_seed"] == tasks[1]["assignment_seed"]
    assert tasks[0]["range_start"] == tasks[1]["range_start"]
    assert tasks[0]["range_end"] == tasks[1]["range_end"]
    assert tasks[0]["network_id"] == tasks[1]["network_id"]
    assert tasks[0]["chain_id"] == tasks[1]["chain_id"]


def test_competitive_round_stops_new_assignments_while_reveal_is_pending(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-competitive-round-pending-validation.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    init_db(db_path)

    first_keys = generate_keypair()
    second_keys = generate_keypair()
    first_miner = register_miner("competitive-pending-a", first_keys["public_key"])
    second_miner = register_miner("competitive-pending-b", second_keys["public_key"])

    first_task = create_next_task(first_miner["miner_id"])
    with get_connection() as connection:
        connection.execute("UPDATE tasks SET status = 'revealed' WHERE task_id = ?", (first_task["task_id"],))
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '[]', 'pending', ?)
            """,
            (
                "job_competitive_pending",
                first_task["task_id"],
                first_miner["miner_id"],
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "2026-06-04T00:00:00+00:00",
            ),
        )

    repeated_first_task = create_next_task(first_miner["miner_id"])
    assert repeated_first_task["task_id"] == first_task["task_id"]

    with pytest.raises(MiningError) as exc_info:
        create_next_task(second_miner["miner_id"])

    assert exc_info.value.status_code == 429
    assert "waiting for validation" in exc_info.value.detail
    with get_connection() as connection:
        second_task_count = connection.execute(
            "SELECT COUNT(*) AS count FROM tasks WHERE miner_id = ?",
            (second_miner["miner_id"],),
        ).fetchone()["count"]
    assert second_task_count == 0

    with get_connection() as connection:
        connection.execute("UPDATE validation_jobs SET status = 'rejected' WHERE job_id = 'job_competitive_pending'")
        connection.execute("UPDATE tasks SET status = 'rejected' WHERE task_id = ?", (first_task["task_id"],))

    second_task = create_next_task(second_miner["miner_id"])
    assert second_task["assignment_mode"] == "competitive_round"
    assert second_task["assignment_seed"] == first_task["assignment_seed"]


def test_competitive_round_reactivates_expired_uncommitted_task(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-competitive-expired-retry.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    init_db(db_path)

    keys = generate_keypair()
    miner = register_miner("competitive-expired-retry", keys["public_key"])
    task = create_next_task(miner["miner_id"])

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET status = 'expired',
                expires_at = '2026-06-01T00:00:00+00:00',
                submitted_at = NULL,
                stale_at = NULL,
                stale_reason = NULL
            WHERE task_id = ?
            """,
            (task["task_id"],),
        )

    retried = create_next_task(miner["miner_id"])

    assert retried["task_id"] == task["task_id"]
    assert retried["status"] == "assigned"
    assert retried["assignment_mode"] == "competitive_round"
    assert retried["assignment_seed"] == task["assignment_seed"]
    assert retried["expires_at"] != "2026-06-01T00:00:00+00:00"
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT status, submitted_at, stale_at, stale_reason
            FROM tasks
            WHERE task_id = ?
            """,
            (task["task_id"],),
        ).fetchone()
        job_count = connection.execute(
            "SELECT COUNT(*) AS count FROM validation_jobs WHERE task_id = ?",
            (task["task_id"],),
        ).fetchone()["count"]
    assert row["status"] == "assigned"
    assert row["submitted_at"] is None
    assert row["stale_at"] is None
    assert row["stale_reason"] is None
    assert job_count == 0


def test_competitive_round_accepts_one_winner_and_marks_late_task_stale(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-competitive-winner.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    monkeypatch.setattr("app.services.rewards.BLOCK_MATURITY_DEPTH", 2)
    monkeypatch.setattr("app.services.mining.BLOCK_MATURITY_DEPTH", 2)
    init_db(db_path)

    first_keys = generate_keypair()
    second_keys = generate_keypair()
    first_miner = register_miner("competitive-winner", first_keys["public_key"])
    second_miner = register_miner("competitive-late", second_keys["public_key"])

    first_task = create_next_task(first_miner["miner_id"])
    second_task = create_next_task(second_miner["miner_id"])

    assert first_task["assignment_seed"] == second_task["assignment_seed"]
    assert first_task["range_start"] == second_task["range_start"]
    assert first_task["range_end"] == second_task["range_end"]

    first_response = _submit_legacy_task(first_task, first_miner["miner_id"], first_keys["private_key"])
    assert first_response["accepted"] is True
    assert first_response["block"]["height"] == 1
    assert first_response["block"]["competitive_round"]["stale_task_ids"] == [second_task["task_id"]]

    late_response = _submit_legacy_task(second_task, second_miner["miner_id"], second_keys["private_key"])
    assert late_response["accepted"] is False
    assert late_response["status"] == "stale"
    assert "competitive round won by" in late_response["message"]

    with get_connection() as connection:
        rows = connection.execute(
            "SELECT task_id, status, stale_reason FROM tasks ORDER BY task_id ASC"
        ).fetchall()
        blocks = connection.execute("SELECT COUNT(*) AS count FROM blocks").fetchone()["count"]
        rewards = connection.execute(
            "SELECT status, COUNT(*) AS count FROM rewards GROUP BY status ORDER BY status"
        ).fetchall()
    by_task = {row["task_id"]: dict(row) for row in rows}

    assert by_task[first_task["task_id"]]["status"] == "accepted"
    assert by_task[second_task["task_id"]]["status"] == "stale"
    assert "competitive round won by" in by_task[second_task["task_id"]]["stale_reason"]
    assert blocks == 1
    assert {row["status"]: row["count"] for row in rewards} == {"immature": 1}
    assert get_full_economic_audit()["valid"] is True


def test_cleanup_closes_obsolete_competitive_job_after_winner_exists(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-competitive-obsolete-job.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    init_db(db_path)

    winner_keys = generate_keypair()
    late_keys = generate_keypair()
    winner_miner = register_miner("competitive-obsolete-winner", winner_keys["public_key"])
    late_miner = register_miner("competitive-obsolete-late", late_keys["public_key"])

    winner_task = create_next_task(winner_miner["miner_id"])
    winner_response = _submit_legacy_task(winner_task, winner_miner["miner_id"], winner_keys["private_key"])
    assert winner_response["accepted"] is True

    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                assignment_seed, assignment_mode, competitive_round_height,
                competitive_round_previous_hash, protocol_params_id, created_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, 'revealed', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task_obsolete_late",
                late_miner["miner_id"],
                winner_task["range_start"],
                winner_task["range_end"],
                winner_task["algorithm"],
                winner_task["assignment_seed"],
                winner_task["assignment_mode"],
                winner_task["competitive_round_height"],
                winner_task["competitive_round_previous_hash"],
                protocol_params_id,
                "2026-06-04T00:00:00+00:00",
                "2099-01-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at
            )
            VALUES ('job_obsolete_late', 'task_obsolete_late', ?, ?, ?, ?, '[]', 'pending', ?)
            """,
            (late_miner["miner_id"], "a" * 64, "b" * 64, "c" * 64, "2026-06-04T00:00:01+00:00"),
        )

    result = cleanup_expired_tasks()

    assert result["closed_competitive_validation_jobs"] == 1
    with get_connection() as connection:
        task = connection.execute(
            "SELECT status, stale_reason FROM tasks WHERE task_id = 'task_obsolete_late'"
        ).fetchone()
        job = connection.execute(
            "SELECT status, result_reason FROM validation_jobs WHERE job_id = 'job_obsolete_late'"
        ).fetchone()
    assert task["status"] == "stale"
    assert "competitive round already won by" in task["stale_reason"]
    assert job["status"] == "rejected"
    assert job["result_reason"] == task["stale_reason"]


def test_cleanup_keeps_current_competitive_job_pending(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-competitive-current-job.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "competitive_round")
    init_db(db_path)

    first_keys = generate_keypair()
    pending_keys = generate_keypair()
    first_miner = register_miner("competitive-current-first", first_keys["public_key"])
    pending_miner = register_miner("competitive-current-pending", pending_keys["public_key"])

    first_task = create_next_task(first_miner["miner_id"])
    first_response = _submit_legacy_task(first_task, first_miner["miner_id"], first_keys["private_key"])
    assert first_response["accepted"] is True
    pending_task = create_next_task(pending_miner["miner_id"])

    with get_connection() as connection:
        connection.execute("UPDATE tasks SET status = 'revealed' WHERE task_id = ?", (pending_task["task_id"],))
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at
            )
            VALUES ('job_current_pending', ?, ?, ?, ?, ?, '[]', 'pending', ?)
            """,
            (
                pending_task["task_id"],
                pending_miner["miner_id"],
                "d" * 64,
                "e" * 64,
                "f" * 64,
                "2026-06-04T00:00:00+00:00",
            ),
        )

    result = cleanup_expired_tasks()

    assert result["closed_competitive_validation_jobs"] == 0
    with get_connection() as connection:
        task = connection.execute(
            "SELECT status FROM tasks WHERE task_id = ?",
            (pending_task["task_id"],),
        ).fetchone()
        job = connection.execute(
            "SELECT status FROM validation_jobs WHERE job_id = 'job_current_pending'"
        ).fetchone()
    assert task["status"] == "revealed"
    assert job["status"] == "pending"


def test_task_assignment_restores_known_miner_identity_after_db_restore(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-restore-miner.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    task = create_next_task(
        "miner_restored_identity",
        public_key=keypair["public_key"],
        name="restored-miner",
    )

    assert task is not None
    assert task["miner_id"] == "miner_restored_identity"
    assert task["status"] == "assigned"
    with get_connection() as connection:
        miner = connection.execute(
            "SELECT miner_id, name, public_key FROM miners WHERE miner_id = ?",
            ("miner_restored_identity",),
        ).fetchone()
    assert miner["name"] == "restored-miner"
    assert miner["public_key"] == keypair["public_key"]


def test_accepted_ranges_protect_start_but_allow_historical_overlap(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-accepted-reuse.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.services.mining.RANGE_START_WINDOW_SIZE", 64)
    monkeypatch.setattr("app.services.mining.RANGE_WINDOW_LOOKAHEAD_MULTIPLIER", 1)
    init_db(db_path)

    first_keys = generate_keypair()
    second_keys = generate_keypair()
    first_miner = register_miner("accepted-history-miner", first_keys["public_key"])
    second_miner = register_miner("active-range-miner", second_keys["public_key"])

    with get_connection() as connection:
        protocol_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            UPDATE protocol_params
            SET max_pi_position = 64, segment_size = 64, range_assignment_max_attempts = 1
            WHERE id = ?
            """,
            (protocol_id,),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, submitted_at
            )
            VALUES ('accepted_full_span', ?, 1, 64, 'bbp_hex_v1', 'accepted', ?, ?, ?)
            """,
            (first_miner["miner_id"], protocol_id, "2026-05-18T00:00:00Z", "2026-05-18T00:00:01Z"),
        )

    task = create_next_task(second_miner["miner_id"])
    assert task["range_start"] != 1
    assert task["range_start"] <= 64
    assert task["range_end"] > 64


def test_active_ranges_still_block_overlapping_assignment(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-active-overlap.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.services.mining.RANGE_START_WINDOW_SIZE", 20)
    monkeypatch.setattr("app.services.mining.RANGE_WINDOW_LOOKAHEAD_MULTIPLIER", 1)
    init_db(db_path)

    first_keys = generate_keypair()
    second_keys = generate_keypair()
    first_miner = register_miner("active-overlap-owner", first_keys["public_key"])
    second_miner = register_miner("active-overlap-candidate", second_keys["public_key"])

    with get_connection() as connection:
        protocol_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            UPDATE protocol_params
            SET max_pi_position = 20, segment_size = 10, range_assignment_max_attempts = 1
            WHERE id = ?
            """,
            (protocol_id,),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, expires_at
            )
            VALUES ('active_overlap', ?, 10, 19, 'bbp_hex_v1', 'assigned', ?, ?, ?)
            """,
            (
                first_miner["miner_id"],
                protocol_id,
                "2026-05-18T00:00:00Z",
                "2099-01-01T00:00:00Z",
            ),
        )

    with pytest.raises(MiningError, match="could not assign a non-overlapping range"):
        create_next_task(second_miner["miner_id"])


def test_RETARGET_MAX_PI_POSITION_caps_task_assignment_range_end(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-retarget-max-pi-position.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.services.mining.RANGE_START_WINDOW_SIZE", 1_200_000)
    monkeypatch.setattr("app.services.mining.RANGE_WINDOW_LOOKAHEAD_MULTIPLIER", 1)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("retarget-cap-miner", keypair["public_key"])
    with get_connection() as connection:
        protocol_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            UPDATE protocol_params
            SET max_pi_position = 2000000,
                RETARGET_MAX_PI_POSITION = 1000000,
                segment_size = 64,
                range_assignment_max_attempts = 5
            WHERE id = ?
            """,
            (protocol_id,),
        )

    task = create_next_task(miner["miner_id"])

    assert task["range_end"] <= 1_000_000


def test_RETARGET_MAX_PI_POSITION_retry_skips_out_of_cap_candidate(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-retarget-cap-retry.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    monkeypatch.setattr(
        mining_service,
        "_range_assignment_window",
        lambda connection, params: {
            "RETARGET_MAX_PI_POSITION": 16,
            "effective_max_pi_position": 40,
            "frontier": 0,
            "lookahead_window": 40,
            "max_start": 40,
            "min_start": 1,
            "window_index": 0,
            "window_size": 40,
        },
    )
    monkeypatch.setattr(mining_service, "sha256_text", lambda value: "13")
    monkeypatch.setattr(mining_service, "_range_is_assignable", lambda connection, start, end, algorithm: True)

    with get_connection() as connection:
        assignment = mining_service._assign_pseudo_random_range(
            connection,
            "miner_retry_cap",
            "task_retry_cap",
            {
                "algorithm": "bbp_hex_v1",
                "range_assignment_max_attempts": 1,
                "segment_size": 8,
            },
        )

    assert assignment["range_start"] == 1
    assert assignment["range_end"] == 8


def test_RETARGET_MAX_PI_POSITION_uppercase_param_caps_assignment_window(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-retarget-cap-uppercase.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.services.mining.RANGE_START_WINDOW_SIZE", 100)
    monkeypatch.setattr("app.services.mining.RANGE_WINDOW_LOOKAHEAD_MULTIPLIER", 1)
    init_db(db_path)

    with get_connection() as connection:
        window = mining_service._range_assignment_window(
            connection,
            {
                "algorithm": "bbp_hex_v1",
                "max_pi_position": 1_000_000,
                "segment_size": 8,
                "RETARGET_MAX_PI_POSITION": 16,
            },
        )

    assert window["RETARGET_MAX_PI_POSITION"] == 16
    assert window["effective_max_pi_position"] == 16
    assert window["max_start"] == 9


def test_RETARGET_MAX_PI_POSITION_reports_when_no_range_available(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-retarget-cap-exhausted.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.services.mining.RANGE_START_WINDOW_SIZE", 8)
    monkeypatch.setattr("app.services.mining.RANGE_WINDOW_LOOKAHEAD_MULTIPLIER", 1)
    init_db(db_path)

    owner_keys = generate_keypair()
    candidate_keys = generate_keypair()
    owner = register_miner("retarget-cap-owner", owner_keys["public_key"])
    candidate = register_miner("retarget-cap-candidate", candidate_keys["public_key"])

    with get_connection() as connection:
        protocol_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            UPDATE protocol_params
            SET max_pi_position = 8,
                RETARGET_MAX_PI_POSITION = 8,
                segment_size = 8,
                range_assignment_max_attempts = 1
            WHERE id = ?
            """,
            (protocol_id,),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, expires_at
            )
            VALUES ('retarget_cap_full', ?, 1, 8, 'bbp_hex_v1', 'assigned', ?, ?, ?)
            """,
            (
                owner["miner_id"],
                protocol_id,
                "2026-05-18T00:00:00Z",
                "2099-01-01T00:00:00Z",
            ),
        )

    with pytest.raises(MiningError, match="RETARGET_MAX_PI_POSITION=8"):
        create_next_task(candidate["miner_id"])


def test_assignment_retires_saturated_start_window(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-window-retirement.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.services.mining.RANGE_START_WINDOW_SIZE", 10)
    monkeypatch.setattr("app.services.mining.RANGE_WINDOW_RETIRE_OCCUPANCY", 0.80)
    monkeypatch.setattr("app.services.mining.RANGE_WINDOW_LOOKAHEAD_MULTIPLIER", 1)
    init_db(db_path)

    keys = generate_keypair()
    historical_miner = register_miner("window-history-miner", keys["public_key"])
    candidate_keys = generate_keypair()
    candidate_miner = register_miner("window-candidate-miner", candidate_keys["public_key"])

    with get_connection() as connection:
        protocol_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        connection.execute(
            """
            UPDATE protocol_params
            SET max_pi_position = 10, segment_size = 2, range_assignment_max_attempts = 1
            WHERE id = ?
            """,
            (protocol_id,),
        )
        for start in range(1, 9):
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, miner_id, range_start, range_end, algorithm, status,
                    protocol_params_id, created_at, submitted_at
                )
                VALUES (?, ?, ?, ?, 'bbp_hex_v1', 'accepted', ?, ?, ?)
                """,
                (
                    f"accepted_start_{start}",
                    historical_miner["miner_id"],
                    start,
                    start + 1,
                    protocol_id,
                    "2026-05-18T00:00:00Z",
                    "2026-05-18T00:00:01Z",
                ),
            )

    task = create_next_task(candidate_miner["miner_id"])

    assert task["range_start"] >= 11


def test_init_db_migrates_global_range_unique_to_protected_start_index(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-range-constraint-migration.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                miner_id TEXT NOT NULL,
                range_start INTEGER NOT NULL,
                range_end INTEGER NOT NULL,
                algorithm TEXT NOT NULL,
                status TEXT NOT NULL,
                assignment_seed TEXT,
                assignment_mode TEXT,
                assignment_ms INTEGER,
                compute_ms INTEGER,
                protocol_params_id INTEGER,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                submitted_at TEXT,
                UNIQUE(range_start, range_end, algorithm)
            )
            """
        )

    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("range-migration-miner", keypair["public_key"])
    with get_connection() as connection:
        protocol_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        indexes = connection.execute("PRAGMA index_list(tasks)").fetchall()
        auto_unique_indexes = [
            index
            for index in indexes
            if index["unique"] and index["origin"] == "u"
        ]
        assert auto_unique_indexes == []
        assert any(index["name"] == "idx_tasks_protected_range_start_unique" for index in indexes)
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, submitted_at
            )
            VALUES ('accepted_reuse_a', ?, 10, 20, 'bbp_hex_v1', 'accepted', ?, ?, ?)
            """,
            (miner["miner_id"], protocol_id, "2026-05-18T00:00:00Z", "2026-05-18T00:00:01Z"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, miner_id, range_start, range_end, algorithm, status,
                    protocol_params_id, created_at, submitted_at
                )
                VALUES ('accepted_reuse_same_start', ?, 10, 30, 'bbp_hex_v1', 'accepted', ?, ?, ?)
                """,
                (miner["miner_id"], protocol_id, "2026-05-18T00:00:02Z", "2026-05-18T00:00:03Z"),
            )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, submitted_at
            )
            VALUES ('accepted_reuse_same_end', ?, 15, 20, 'bbp_hex_v1', 'accepted', ?, ?, ?)
            """,
            (miner["miner_id"], protocol_id, "2026-05-18T00:00:02Z", "2026-05-18T00:00:03Z"),
        )


def test_validation_job_restores_known_validator_identity_after_db_restore(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-restore-validator.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner_keys = generate_keypair()
    miner = register_miner("assignment-miner", miner_keys["public_key"])
    task = create_next_task(miner["miner_id"])
    validator_keys = generate_keypair()

    with get_connection() as connection:
        connection.execute("UPDATE tasks SET status = 'revealed' WHERE task_id = ?", (task["task_id"],))
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                "job_restore_validator",
                task["task_id"],
                miner["miner_id"],
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "[]",
                "2026-05-18T00:00:00Z",
            ),
            )

    _heartbeat_validator(validator_keys, "validator_restored_identity", name="restored-validator")
    job = get_validation_job(
        "validator_restored_identity",
        public_key=validator_keys["public_key"],
        name="restored-validator",
    )

    assert job is not None
    assert job["job_id"] == "job_restore_validator"
    assert job["assigned_validator_id"] == "validator_restored_identity"
    with get_connection() as connection:
        validator = connection.execute(
            "SELECT validator_id, name, public_key, stake_locked FROM validators WHERE validator_id = ?",
            ("validator_restored_identity",),
        ).fetchone()
    assert validator["name"] == "restored-validator"
    assert validator["public_key"] == validator_keys["public_key"]
    assert validator["stake_locked"] >= 31.416


def test_validation_job_broadcasts_global_pending_job(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-validator-fallback.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner_keys = generate_keypair()
    validator_keys = generate_keypair()
    miner = register_miner("assignment-miner", miner_keys["public_key"])
    task = create_next_task(miner["miner_id"])

    with get_connection() as connection:
        connection.execute(
            "INSERT INTO validators (validator_id, name, public_key, registered_at) VALUES (?, ?, ?, ?)",
            ("validator_fallback_identity", "fallback-validator", validator_keys["public_key"], "2026-05-18T00:00:00Z"),
        )
        connection.execute("UPDATE tasks SET status = 'revealed' WHERE task_id = ?", (task["task_id"],))
        connection.execute(
            """
            INSERT INTO validation_jobs (
                job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
                samples, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                "job_fallback_validator",
                task["task_id"],
                miner["miner_id"],
                "d" * 64,
                "e" * 64,
                "f" * 64,
                "[]",
                "2026-05-18T00:00:00Z",
            ),
            )

    _heartbeat_validator(validator_keys, "validator_fallback_identity", node_id="fallback-node")
    job = get_validation_job("validator_fallback_identity")

    assert job is not None
    assert job["job_id"] == "job_fallback_validator"
    assert job["selection_score"] is not None
