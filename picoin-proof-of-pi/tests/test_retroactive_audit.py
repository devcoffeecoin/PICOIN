from app.core.crypto import hash_result
from app.core.pi import calculate_pi_segment
from app.core.signatures import build_submission_signature_payload, generate_keypair, sign_payload
from app.db.database import init_db
from app.services.mining import (
    create_next_task,
    get_recent_events,
    get_retroactive_audits,
    register_miner,
    run_retroactive_audit,
    submit_task,
)


def test_retroactive_audit_uses_double_protocol_samples(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retroactive-audit.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("retro-miner", keypair["public_key"])
    _mine_legacy_block(miner["miner_id"], keypair["private_key"])

    result = run_retroactive_audit(sample_multiplier=2)
    audits = get_retroactive_audits()
    events = get_recent_events()

    assert result["accepted"] is True
    assert result["audit"]["sample_count"] == 64
    assert result["audit"]["passed"] is True
    assert len(result["audit"]["samples"]) == 64
    assert audits[0]["id"] == result["audit"]["id"]
    assert any(event["type"] == "retroactive_audit" for event in events)


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
