from pathlib import Path

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
from app.services.difficulty_service import DifficultyService


def test_services_wire_RETARGET_MAX_PI_POSITION_name() -> None:
    service_sources = (
        Path("app/services/mining.py").read_text(),
        Path("app/services/difficulty_service.py").read_text(),
    )

    assert all("RETARGET_MAX_PI_POSITION" in source for source in service_sources)
    assert all("retarget_max_pi_position" not in source for source in service_sources)


def test_protocol_exposes_dynamic_difficulty_and_rewards(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "protocol.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    protocol = get_protocol()
    history = get_protocol_history()

    assert protocol["protocol_version"] == "0.18"
    assert protocol["required_validator_approvals"] == 3
    assert protocol["sample_count"] == 32
    assert protocol["difficulty"] == 4.0
    assert protocol["reward_per_block"] == protocol["base_reward"]
    assert protocol["proof_of_pi_reward_percent"] == 0.80
    assert protocol["science_compute_reward_percent"] == 0.07
    assert protocol["validator_auditor_reward_percent"] == 0.10
    assert protocol["scientific_development_reward_percent"] == 0.03
    assert protocol["proof_of_pi_reward_per_block"] == 2.51328
    assert protocol["scientific_development_treasury_per_block"] == 0.094248
    assert protocol["RETARGET_MAX_PI_POSITION"] == 1_000_000
    assert history[0]["active"] is True
    assert history[0]["difficulty"] == protocol["difficulty"]
    assert history[0]["RETARGET_MAX_PI_POSITION"] == 1_000_000


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
    assert response["block"]["reward"] == protocol["proof_of_pi_reward_per_block"]
    assert response["block"]["protocol_version"] == protocol["protocol_version"]


def test_retarget_increases_difficulty_after_fast_epoch(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-fast.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("retarget-miner", keypair["public_key"])
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=1_000, total_block_ms=30_000, count=20)

    before = get_protocol()
    result = run_retarget()
    after = get_protocol()
    history = get_retarget_history()
    status = get_difficulty_status()

    assert result["retargeted"] is True
    assert result["event"]["action"] == "increase"
    assert after["difficulty"] > before["difficulty"]
    assert after["segment_size"] > before["segment_size"]
    assert history[0]["epoch_block_count"] == 20
    assert status["last_retarget_height"] == 20


def test_retarget_preserves_RETARGET_MAX_PI_POSITION(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-preserves-max-pi-position.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("retarget-cap-preserve-miner", keypair["public_key"])
    with get_connection() as connection:
        connection.execute(
            "UPDATE protocol_params SET RETARGET_MAX_PI_POSITION = 123456 WHERE active = 1"
        )
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=1_000, total_block_ms=30_000, count=20)

    result = run_retarget()
    protocol = get_protocol()
    history = get_protocol_history()

    assert result["retargeted"] is True
    assert protocol["RETARGET_MAX_PI_POSITION"] == 123_456
    assert history[0]["RETARGET_MAX_PI_POSITION"] == 123_456


def test_retarget_preview_does_not_mutate_protocol_or_history(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-preview.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("preview-miner", keypair["public_key"])
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=1_000, total_block_ms=30_000, count=20)

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
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=1_000, total_block_ms=30_000, count=19)

    result = run_retarget()
    preview = preview_retarget()
    status = get_difficulty_status()

    assert result["retargeted"] is False
    assert preview["ready"] is False
    assert preview["blocks_until_ready"] == 1
    assert status["epoch_blocks_required"] == 20
    assert status["blocks_until_ready"] == 1
    assert status["blocks_until_next_epoch"] == 1
    assert get_protocol()["difficulty"] == 4.0


def test_retarget_decreases_difficulty_after_slow_epoch(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-slow.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("slow-miner", keypair["public_key"])
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=100_000, total_block_ms=120_000, count=20)

    before = get_protocol()
    result = run_retarget()
    after = get_protocol()

    assert result["retargeted"] is True
    assert result["event"]["action"] == "decrease"
    assert after["difficulty"] < before["difficulty"]
    assert after["segment_size"] < before["segment_size"]


def test_retarget_decreases_sample_count_when_validation_dominates(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-validation-slow.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("validation-slow-miner", keypair["public_key"])
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=10_000, validation_ms=85_000, total_block_ms=100_000, count=20)

    before = get_protocol()
    result = run_retarget()
    after = get_protocol()

    assert result["retargeted"] is True
    assert "validation bottleneck" in result["event"]["reason"]
    assert after["sample_count"] < before["sample_count"]
    assert after["difficulty"] == before["difficulty"]
    assert after["segment_size"] == before["segment_size"]


def test_retarget_increases_sample_count_when_blocks_fast_and_validation_cheap(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-validation-cheap.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("validation-cheap-miner", keypair["public_key"])
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=25_000, validation_ms=1_000, total_block_ms=30_000, count=20)

    before = get_protocol()
    result = run_retarget()
    after = get_protocol()

    assert result["retargeted"] is True
    assert "validation cheap" in result["event"]["reason"]
    assert after["sample_count"] > before["sample_count"]
    assert after["difficulty"] > before["difficulty"]
    assert after["segment_size"] > before["segment_size"]


def test_retarget_clamps_adjustment_and_uses_total_block_time(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-clamp.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("clamp-miner", keypair["public_key"])
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=10_000, total_block_ms=117_470, count=20)

    before = get_protocol()
    result = run_retarget()
    after = get_protocol()

    assert result["retargeted"] is True
    assert result["event"]["action"] == "decrease"
    assert result["event"]["adjustment_factor"] == 0.85
    assert after["difficulty"] == round(before["difficulty"] * 0.85, 6)


def test_retarget_persists_protocol_metadata_and_bucket_metrics(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-persistence.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("persist-miner", keypair["public_key"])
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=100_000, validation_ms=5_000, total_block_ms=120_000, count=20)

    result = run_retarget()

    assert result["retargeted"] is True
    with get_connection() as connection:
        protocol = connection.execute(
            """
            SELECT difficulty, segment_size, sample_count, target_block_time_ms,
                   retarget_reason, retarget_source_window, retarget_source_details,
                   previous_protocol_params_id
            FROM protocol_params
            WHERE active = 1
            """
        ).fetchone()
        metric = connection.execute("SELECT * FROM difficulty_bucket_metrics ORDER BY samples_seen DESC LIMIT 1").fetchone()
    assert protocol["target_block_time_ms"] == 60_000
    assert protocol["retarget_reason"]
    assert protocol["retarget_source_window"] == 20
    assert protocol["retarget_source_details"]
    assert protocol["previous_protocol_params_id"] is not None
    assert metric is not None
    assert metric["samples_seen"] > 0


def test_difficulty_service_is_deterministic_for_same_history() -> None:
    params = {"difficulty": 0.125, "segment_size": 64, "sample_count": 32, "max_pi_position": 10000}
    history = [
        {
            "range_start": 1000,
            "range_end": 1063,
            "segment_size": 64,
            "sample_count": 32,
            "difficulty": 0.125,
            "total_task_ms": 100_000,
            "validation_ms": 5_000,
            "total_block_ms": 117_470,
        }
        for _ in range(20)
    ]

    first = DifficultyService.calculate_next_protocol_params(history, params, 1000)
    second = DifficultyService.calculate_next_protocol_params(history, params, 1000)

    assert first == second
    assert first[0]["difficulty"] == 0.10625


def test_bucket_history_drives_target_range_retarget() -> None:
    params = {"difficulty": 0.125, "segment_size": 64, "sample_count": 32, "max_pi_position": 10000}
    history = []
    for _ in range(10):
        history.append(
            {
                "range_start": 1000,
                "range_end": 1063,
                "segment_size": 64,
                "sample_count": 32,
                "difficulty": 0.125,
                "total_task_ms": 100_000,
                "validation_ms": 1_000,
                "total_block_ms": 120_000,
            }
        )
        history.append(
            {
                "range_start": 100000,
                "range_end": 100063,
                "segment_size": 64,
                "sample_count": 32,
                "difficulty": 0.125,
                "total_task_ms": 10_000,
                "validation_ms": 1_000,
                "total_block_ms": 30_000,
            }
        )

    next_params, meta = DifficultyService.calculate_next_protocol_params(history, params, 1000)

    assert meta["bucket"] == "1000-2500"
    assert meta["avg_total_block_ms"] == 120000.0
    assert next_params["difficulty"] < params["difficulty"]


def test_retarget_ignores_blocks_without_safe_total_timing(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retarget-invalid-timing.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("invalid-timing-miner", keypair["public_key"])
    _insert_epoch_blocks(miner["miner_id"], total_task_ms=0, validation_ms=0, total_block_ms=0, count=20)

    result = run_retarget(force=True)

    assert result["retargeted"] is False


def _insert_epoch_blocks(
    miner_id: str,
    total_task_ms: int,
    total_block_ms: int | None = None,
    validation_ms: int = 1,
    count: int = 5,
) -> None:
    total_block_ms = total_task_ms if total_block_ms is None else total_block_ms
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
                    task_id, protocol_params_id, protocol_version, validation_mode, total_task_ms, total_block_ms, validation_ms
                )
                VALUES (?, ?, ?, ?, ?, 'bbp_hex_v1', ?, '[]', ?, ?, 3.14159, 1.0, ?, ?, '0.9', 'external_commit_reveal', ?, ?, ?)
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
                    total_block_ms,
                    validation_ms,
                ),
            )
            previous_hash = block_hash
