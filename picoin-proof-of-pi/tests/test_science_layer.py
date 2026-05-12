import pytest

from app.core.crypto import hash_result
from app.core.pi import calculate_pi_segment
from app.core.signatures import build_submission_signature_payload, generate_keypair, sign_payload
from app.db.database import get_connection, init_db
from app.services.mining import create_next_task, register_miner, submit_task
from app.services.science import (
    ScienceError,
    approve_science_reserve_activation,
    create_science_job,
    execute_science_reserve_activation,
    get_science_events,
    get_science_reserve,
    pay_science_worker,
    propose_science_reserve_activation,
    record_science_reserve_for_block,
    stake_science_access,
    transition_science_job,
    unstake_science_access,
)


def test_science_user_without_stake_cannot_create_job(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-no-stake.sqlite3")

    with pytest.raises(ScienceError):
        create_science_job("addr-no-stake", "ai_inference", "meta_hash", "ipfs://job", 0)


def test_science_stake_tiers_are_derived_from_amount(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-tiers.sqlite3")

    researcher = stake_science_access("addr-researcher", 3_141.6)
    lab = stake_science_access("addr-lab", 31_416)
    institution = stake_science_access("addr-institution", 314_160)

    assert researcher["tier"] == "researcher"
    assert researcher["compute_multiplier"] == 1
    assert lab["tier"] == "lab"
    assert lab["compute_multiplier"] == 10
    assert institution["tier"] == "institution"
    assert institution["compute_multiplier"] == 100


def test_science_reserve_accumulates_twenty_percent_on_accepted_block(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-reserve-block.sqlite3")

    keypair = generate_keypair()
    miner = register_miner("science-reserve-miner", keypair["public_key"])
    _mine_legacy_block(miner["miner_id"], keypair["private_key"])
    reserve = get_science_reserve()

    assert reserve["total_reserved"] == 0.62832
    assert reserve["total_paid"] == 0.0
    assert reserve["available"] == 0.62832
    assert reserve["status"] == "RESERVE_LOCKED"


def test_science_reserve_locked_blocks_budget_reservation_and_worker_payment(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-locked-reserve.sqlite3")
    _seed_science_reserve(25)
    stake_science_access("addr-locked", 31_416)

    with pytest.raises(ScienceError):
        create_science_job("addr-locked", "ai_inference", "metadata_hash", "ipfs://payload", 5)

    job = create_science_job("addr-locked", "ai_inference", "metadata_hash", "ipfs://payload", 0)
    transition_science_job(job["job_id"], "queued")
    transition_science_job(job["job_id"], "assigned", worker_address="worker-locked")
    transition_science_job(job["job_id"], "committed", worker_address="worker-locked")
    transition_science_job(
        job["job_id"],
        "submitted",
        worker_address="worker-locked",
        result_hash="result_hash",
        proof_hash="proof_hash",
    )
    transition_science_job(job["job_id"], "verified")
    transition_science_job(job["job_id"], "accepted")

    with pytest.raises(ScienceError):
        pay_science_worker(job["job_id"])


def test_science_rejected_job_does_not_pay_worker(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-rejected.sqlite3")
    _seed_science_reserve(25)
    stake_science_access("addr-reject", 3_141.6)

    job = create_science_job("addr-reject", "simulation", "meta_hash", "s3://job", 0)
    rejected = transition_science_job(job["job_id"], "rejected")

    assert rejected["status"] == "rejected"
    with pytest.raises(ScienceError):
        pay_science_worker(job["job_id"])
    assert get_science_reserve()["total_paid"] == 0.0


def test_science_accepted_job_pays_worker_once(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-paid.sqlite3")
    monkeypatch.setattr("app.services.science.SCIENCE_RESERVE_GOVERNANCE_TIMELOCK_SECONDS", 0)
    _seed_science_reserve(25)
    _activate_science_reserve()
    stake_science_access("addr-requester", 31_416)

    job = create_science_job("addr-requester", "ai_inference", "metadata_hash", "ipfs://payload", 5)
    transition_science_job(job["job_id"], "queued")
    transition_science_job(job["job_id"], "assigned", worker_address="worker-1")
    transition_science_job(job["job_id"], "committed", worker_address="worker-1")
    transition_science_job(
        job["job_id"],
        "submitted",
        worker_address="worker-1",
        result_hash="result_hash",
        proof_hash="proof_hash",
    )
    transition_science_job(job["job_id"], "verified")
    accepted = transition_science_job(job["job_id"], "accepted")
    paid = pay_science_worker(job["job_id"])
    reserve = get_science_reserve()

    assert accepted["status"] == "accepted"
    assert paid["paid"] is True
    assert paid["paid_amount"] == 5
    assert reserve["total_paid"] == 5
    assert reserve["total_pending"] == 0
    with pytest.raises(ScienceError):
        pay_science_worker(job["job_id"])


def test_science_reserve_activation_requires_multisig_and_timelock(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-governance.sqlite3")
    monkeypatch.setattr("app.services.science.SCIENCE_RESERVE_GOVERNANCE_TIMELOCK_SECONDS", 0)

    proposed = propose_science_reserve_activation("signer-1")
    assert proposed["status"] == "RESERVE_LOCKED"
    assert proposed["approvals"] == ["signer-1"]
    with pytest.raises(ScienceError):
        execute_science_reserve_activation()

    approved = approve_science_reserve_activation("signer-2")
    assert set(approved["approvals"]) == {"signer-1", "signer-2"}
    activated = execute_science_reserve_activation()

    assert activated["status"] == "L2_MARKETPLACE_ACTIVE"


def test_science_unstake_is_blocked_with_active_jobs(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-unstake.sqlite3")
    stake_science_access("addr-active", 3_141.6)
    create_science_job("addr-active", "ai_inference", "metadata_hash", "ipfs://payload", 0)

    with pytest.raises(ScienceError):
        unstake_science_access("addr-active")


def test_science_events_are_generated_for_l2_sync(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-events.sqlite3")
    _seed_science_reserve(10)
    stake_science_access("addr-events", 3_141.6)
    job = create_science_job("addr-events", "ai_inference", "metadata_hash", "ipfs://payload", 0)
    transition_science_job(job["job_id"], "rejected")

    event_types = {event["type"] for event in get_science_events(10)}

    assert "ScienceStakeUpdated" in event_types
    assert "ScienceJobCreated" in event_types
    assert "ScienceJobRejected" in event_types


def _init_science_db(tmp_path, monkeypatch, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)


def _seed_science_reserve(block_reward: float) -> None:
    with get_connection() as connection:
        record_science_reserve_for_block(connection, 1, block_reward)


def _activate_science_reserve() -> None:
    propose_science_reserve_activation("signer-1")
    approve_science_reserve_activation("signer-2")
    execute_science_reserve_activation()


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
