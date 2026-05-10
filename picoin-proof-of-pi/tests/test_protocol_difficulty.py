from app.core.crypto import hash_result
from app.core.pi import calculate_pi_segment
from app.core.signatures import build_submission_signature_payload, generate_keypair, sign_payload
from app.db.database import init_db
from app.services.mining import create_next_task, get_protocol, get_protocol_history, register_miner, submit_task


def test_protocol_exposes_dynamic_difficulty_and_rewards(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "protocol.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    protocol = get_protocol()
    history = get_protocol_history()

    assert protocol["protocol_version"] == "0.8"
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
