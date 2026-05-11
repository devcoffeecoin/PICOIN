from app.core.crypto import hash_result
from app.core.pi import calculate_pi_segment
from app.core.signatures import build_submission_signature_payload, generate_keypair, sign_payload
from app.db.database import get_connection, init_db
from app.services.mining import (
    create_next_task,
    get_difficulty_status,
    get_protocol,
    get_protocol_history,
    get_retarget_history,
    preview_retarget,
    register_miner,
    run_retarget,
    submit_task,
)


def test_protocol_exposes_dynamic_difficulty_and_rewards(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "protocol.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    protocol = get_protocol()
    history = get_protocol_history()

    assert protocol["protocol_version"] == "0.12"
    assert protocol["difficulty"] == 1.0
    assert protocol["reward_per_block"] == protocol["base_reward"]
    assert history[0]["active"] is True
    assert history[0]["difficulty"] == protocol["difficulty"]


def test_accepted_block_records_protocol_difficulty(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "block-difficulty.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("difficulty-miner", keypair["public_key"])
    task = create_next_task(miner["miner_id"])
    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    signed_at = "2026-05-10T00:00:00+00:00"
    payload = build_submission_signature_payload(
        task_id=task["task_id"],
        miner_id=miner["miner_id"],
        range_start=task["range_start"],
        range_end=task["range_end"],
        algorithm=task["algorithm"],
        result_hash=result_hash,
        signed_at=signed_at,
    )
    signature = sign_payload(keypair["private_key"], payload)

    response = submit_task(
        task_id=task["task_id"],
        miner_id=miner["miner_id"],
        result_hash=result_hash,
        segment=segment,
        signature=signature,
        signed_at=signed_at,
    )

    protocol = get_protocol()
    assert response["accepted"] is True
    assert response["block"]["difficulty"] == protocol["difficulty"]
    assert response["block"]["reward"] == protocol["reward_per_block"]
    assert response["block"]["protocol_version"] == protocol["protocol_version"]


def test_retarget_increases_difficulty_after_fast_epoch(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-fast.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("retarget-miner", keypair["public_key"])
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=1_000)

    before = get_protocol()
    result = run_retarget()
    after = get_protocol()
    history = get_retarget_history()
    status = get_difficulty_status()

    assert result["retargeted"] is True
    assert result["event"]["action"] == "increase"
    assert after["difficulty"] > before["difficulty"]
    assert after["segment_size"] > before["segment_size"]
    assert history[0]["epoch_block_count"] == 5
    assert status["last_retarget_height"] == 5


def test_retarget_preview_does_not_mutate_protocol_or_history(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-preview.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("preview-miner", keypair["public_key"])
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=1_000)

    before = get_protocol()
    preview = preview_retarget()
    after = get_protocol()

    assert preview["ready"] is True
    assert preview["action"] == "increase"
    assert preview["proposed_protocol"]["difficulty"] > preview["current_protocol"]["difficulty"]
    assert after == before
    assert get_retarget_history() == []


def test_retarget_waits_until_epoch_is_complete(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-wait.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("waiting-miner", keypair["public_key"])
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=1_000, count=4)

    result = run_retarget()
    preview = preview_retarget()

    assert result["retargeted"] is False
    assert preview["ready"] is False
    assert preview["blocks_until_ready"] == 1
    assert get_protocol()["difficulty"] == 1.0


def test_retarget_decreases_difficulty_after_slow_epoch(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-slow.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("slow-miner", keypair["public_key"])
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=100_000)

    before = get_protocol()
    result = run_retarget()
    after = get_protocol()

    assert result["retargeted"] is True
    assert result["event"]["action"] == "decrease"
    assert after["difficulty"] < before["difficulty"]
    assert after["segment_size"] < before["segment_size"]


def _insert_epoch_blocks(miner_id: str, total_task_ms: int, count: int = 5) -> None:
    with get_connection() as connection:
        protocol_params_id = connection.execute(
            "SELECT id FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        previous_hash = "0" * 64
        for height in range(1, count + 1):
            task_id = f"task_retarget_{height}"
            range_start = (height - 1) * 64 + 1
            range_end = range_start + 63
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, miner_id, range_start, range_end, algorithm, status,
                    protocol_params_id, created_at, submitted_at
                )
                VALUES (?, ?, ?, ?, 'bbp_hex_v1', 'accepted', ?, ?, ?)
                """,
                (
                    task_id,
                    miner_id,
                    range_start,
                    range_end,
                    protocol_params_id,
                    "2026-05-10T00:00:00+00:00",
                    "2026-05-10T00:00:01+00:00",
                ),
            )
            block_hash = f"{height:064x}"
            connection.execute(
                """
                INSERT INTO blocks (
                    height, previous_hash, miner_id, range_start, range_end, algorithm,
                    result_hash, samples, timestamp, block_hash, reward, difficulty,
                    task_id, protocol_params_id, protocol_version, validation_mode, total_task_ms, validation_ms
                )
                VALUES (?, ?, ?, ?, ?, 'bbp_hex_v1', ?, '[]', ?, ?, 3.14159, 1.0, ?, ?, '0.9', 'external_commit_reveal', ?, 1)
                """,
                (
                    height,
                    previous_hash,
                    miner_id,
                    range_start,
                    range_end,
                    f"{height + 100:064x}",
                    "2026-05-10T00:00:01+00:00",
                    block_hash,
                    task_id,
                    protocol_params_id,
                    total_task_ms,
                ),
            )
            previous_hash = block_hash
