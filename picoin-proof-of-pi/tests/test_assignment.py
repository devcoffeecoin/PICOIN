from app.core.signatures import generate_keypair
from app.db.database import get_connection
from app.db.database import init_db
from app.services.mining import create_next_task, get_validation_job, register_miner


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

    job = get_validation_job("validator_fallback_identity")

    assert job is not None
    assert job["job_id"] == "job_fallback_validator"
    assert job["selection_score"] is None
