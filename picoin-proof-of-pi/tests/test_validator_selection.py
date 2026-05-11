from datetime import datetime, timezone

from app.core.signatures import generate_keypair
from app.db.database import get_connection, init_db
from app.services.mining import get_validation_job, get_validators, register_miner, register_validator


def test_weighted_validator_selection_excludes_lowest_rank_when_pool_is_full(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "validator-selection.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner = _register_miner("selection-miner")
    validators = [_register_validator(f"selection-validator-{index}") for index in range(5)]
    low_validator = validators[-1]
    with get_connection() as connection:
        connection.execute(
            "UPDATE validators SET trust_score = 0.25 WHERE validator_id = ?",
            (low_validator["validator_id"],),
        )
        _insert_pending_job(connection, miner["miner_id"])

    low_job = get_validation_job(low_validator["validator_id"])
    high_job = get_validation_job(validators[0]["validator_id"])

    assert low_job is None
    assert high_job is not None
    assert high_job["selection_rank"] <= 4
    assert high_job["selection_score"] > 0


def test_validator_list_exposes_rotation_aware_selection_scores(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "validator-selection-score.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner = _register_miner("rotation-miner")
    vote_history_miner = _register_miner("vote-history-miner")
    fresh_validator = _register_validator("fresh-validator")
    busy_validator = _register_validator("busy-validator")
    with get_connection() as connection:
        _insert_pending_job(connection, miner["miner_id"], suffix="rotation")
        _insert_recent_votes(connection, vote_history_miner["miner_id"], busy_validator["validator_id"], count=3)

    validators = {validator["validator_id"]: validator for validator in get_validators()}

    assert validators[fresh_validator["validator_id"]]["selection_score"] > validators[busy_validator["validator_id"]]["selection_score"]
    assert validators[busy_validator["validator_id"]]["recent_validation_votes"] == 3
    assert validators[fresh_validator["validator_id"]]["availability_score"] >= 0.5


def _register_miner(name: str) -> dict:
    keypair = generate_keypair()
    return register_miner(name, keypair["public_key"])


def _register_validator(name: str) -> dict:
    keypair = generate_keypair()
    return register_validator(name, keypair["public_key"])


def _insert_pending_job(connection, miner_id: str, suffix: str = "1") -> None:
    protocol_params_id = connection.execute(
        "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()["id"]
    now = datetime.now(timezone.utc).isoformat()
    task_id = f"task_selection_{suffix}"
    job_id = f"job_selection_{suffix}"
    offset = sum(ord(char) for char in suffix) * 100
    range_start = 1000 + offset
    range_end = range_start + 63
    connection.execute(
        """
        INSERT INTO tasks (
            task_id, miner_id, range_start, range_end, algorithm, status,
            protocol_params_id, created_at
        )
        VALUES (?, ?, ?, ?, 'bbp_hex_v1', 'revealed', ?, ?)
        """,
        (task_id, miner_id, range_start, range_end, protocol_params_id, now),
    )
    connection.execute(
        """
        INSERT INTO validation_jobs (
            job_id, task_id, miner_id, result_hash, merkle_root, challenge_seed,
            samples, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, '[]', 'pending', ?)
        """,
        (job_id, task_id, miner_id, "a" * 64, "b" * 64, "c" * 64, now),
    )


def _insert_recent_votes(connection, miner_id: str, validator_id: str, count: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for index in range(count):
        suffix = f"vote_{index}"
        _insert_pending_job(connection, miner_id, suffix=suffix)
        connection.execute(
            """
            INSERT INTO validation_votes (
                job_id, task_id, validator_id, approved, reason, signature,
                signed_at, validation_ms, created_at
            )
            VALUES (?, ?, ?, 1, 'history', 'signature', ?, 1, ?)
            """,
            (
                f"job_selection_{suffix}",
                f"task_selection_{suffix}",
                validator_id,
                now,
                now,
            ),
        )
