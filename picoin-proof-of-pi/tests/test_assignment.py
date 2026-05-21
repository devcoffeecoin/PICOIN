import sqlite3

import pytest

from app.core.signatures import generate_keypair, sign_payload
from app.db.database import get_connection
from app.db.database import init_db
from app.services.mining import MiningError, create_next_task, get_validation_job, record_validator_heartbeat, register_miner


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


def test_pseudo_random_assignment_returns_non_sequential_ranges(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
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


def test_validation_job_falls_back_to_global_pending_job(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment-validator-fallback.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.services.mining._selected_validators_for_job", lambda connection, job, params: [])
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
    assert job["selection_score"] is None
