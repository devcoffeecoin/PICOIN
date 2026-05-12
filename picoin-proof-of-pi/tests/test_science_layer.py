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
    pause_science_reserve,
    propose_science_reserve_activation,
    record_science_reserve_for_block,
    stake_science_access,
    transition_science_job,
    unpause_science_reserve,
    unstake_science_access,
)


def test_science_user_without_stake_cannot_create_job(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-no-stake.sqlite3")

    with pytest.raises(ScienceError):
        create_science_job("addr-no-stake", "ai_inference", "meta_hash", "ipfs://job", max_compute_units=1, reward_per_compute_unit=1, max_reward=1)


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
    assert reserve["payouts_enabled"] is False
    assert reserve["emergency_paused"] is False


def test_science_create_job_uses_compute_units_and_caps_payout(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-compute-units.sqlite3")
    _seed_science_reserve(100)
    stake_science_access("addr-compute", 31_416)

    job = create_science_job(
        "addr-compute",
        "ai_inference",
        "metadata_hash",
        "ipfs://payload",
        max_compute_units=10,
        reward_per_compute_unit=2,
        max_reward=15,
    )

    assert job["max_compute_units"] == 10
    assert job["reward_per_compute_unit"] == 2
    assert job["max_reward"] == 15
    assert job["reward_budget"] == 15
    assert get_science_reserve()["total_pending"] == 15


def test_science_reserve_locked_blocks_worker_payment(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-locked-reserve.sqlite3")
    _seed_science_reserve(25)
    stake_science_access("addr-locked", 31_416)

    job = create_science_job(
        "addr-locked",
        "ai_inference",
        "metadata_hash",
        "ipfs://payload",
        max_compute_units=10,
        reward_per_compute_unit=0.5,
        max_reward=5,
    )
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
    accepted = transition_science_job(job["job_id"], "accepted", compute_units_used=8)

    assert accepted["payout_amount"] == 4
    with pytest.raises(ScienceError):
        pay_science_worker(job["job_id"])


def test_science_rejected_job_does_not_pay_worker(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-rejected.sqlite3")
    _seed_science_reserve(25)
    stake_science_access("addr-reject", 3_141.6)

    job = create_science_job("addr-reject", "simulation", "meta_hash", "s3://job", max_compute_units=1, reward_per_compute_unit=1, max_reward=1)
    rejected = transition_science_job(job["job_id"], "rejected")

    assert rejected["status"] == "rejected"
    assert get_science_reserve()["total_pending"] == 0
    with pytest.raises(ScienceError):
        pay_science_worker(job["job_id"])
    assert get_science_reserve()["total_paid"] == 0.0


def test_science_accepted_job_pays_worker_once(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-paid.sqlite3")
    monkeypatch.setattr("app.services.science.SCIENCE_RESERVE_GOVERNANCE_TIMELOCK_SECONDS", 0)
    _seed_science_reserve(25)
    _activate_science_reserve()
    stake_science_access("addr-requester", 31_416)

    job = create_science_job(
        "addr-requester",
        "ai_inference",
        "metadata_hash",
        "ipfs://payload",
        max_compute_units=10,
        reward_per_compute_unit=1,
        max_reward=5,
    )
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
    accepted = transition_science_job(job["job_id"], "accepted", compute_units_used=9)
    paid = pay_science_worker(job["job_id"])
    reserve = get_science_reserve()

    assert accepted["status"] == "accepted"
    assert accepted["payout_amount"] == 5
    assert paid["paid"] is True
    assert paid["status"] == "paid"
    assert paid["paid_amount"] == 5
    assert reserve["total_paid"] == 5
    assert reserve["total_pending"] == 0
    with pytest.raises(ScienceError):
        pay_science_worker(job["job_id"])


def test_science_reserve_activation_requires_multisig_and_timelock(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-governance.sqlite3")
    monkeypatch.setattr("app.services.science.SCIENCE_RESERVE_GOVERNANCE_TIMELOCK_SECONDS", 0)

    proposed = propose_science_reserve_activation("signer-1")
    assert proposed["status"] == "L2_PENDING"
    assert proposed["approvals"] == ["signer-1"]
    with pytest.raises(ScienceError):
        execute_science_reserve_activation()

    approved = approve_science_reserve_activation("signer-2")
    assert set(approved["approvals"]) == {"signer-1", "signer-2"}
    activated = execute_science_reserve_activation()

    assert activated["status"] == "L2_ACTIVE"
    assert activated["payouts_enabled"] is True


def test_science_unstake_is_blocked_with_active_jobs(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-unstake.sqlite3")
    stake_science_access("addr-active", 3_141.6)
    create_science_job("addr-active", "ai_inference", "metadata_hash", "ipfs://payload", max_compute_units=0, reward_per_compute_unit=0, max_reward=0)

    with pytest.raises(ScienceError):
        unstake_science_access("addr-active")


def test_science_events_are_generated_for_l2_sync(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-events.sqlite3")
    _seed_science_reserve(10)
    stake_science_access("addr-events", 3_141.6)
    job = create_science_job("addr-events", "ai_inference", "metadata_hash", "ipfs://payload", max_compute_units=0, reward_per_compute_unit=0, max_reward=0)
    transition_science_job(job["job_id"], "rejected")

    event_types = {event["type"] for event in get_science_events(10)}

    assert "ScienceStakeUpdated" in event_types
    assert "ScienceJobCreated" in event_types
    assert "ScienceJobRejected" in event_types


def test_science_reserve_payouts_disabled_and_emergency_pause_block_payment(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-pause.sqlite3")
    monkeypatch.setattr("app.services.science.SCIENCE_RESERVE_GOVERNANCE_TIMELOCK_SECONDS", 0)
    _seed_science_reserve(100)
    _activate_science_reserve()
    stake_science_access("addr-paused", 31_416)
    job = _accepted_job("addr-paused", "worker-paused", compute_units_used=2)

    pause = pause_science_reserve("signer-1")
    assert pause["status"] == "EMERGENCY_PAUSED"
    assert pause["emergency_paused"] is True
    with pytest.raises(ScienceError):
        pay_science_worker(job["job_id"])

    unpause = unpause_science_reserve("signer-2")
    assert unpause["status"] == "L2_ACTIVE"
    with get_connection() as connection:
        connection.execute("UPDATE science_reserve_governance SET payouts_enabled = 0 WHERE id = 1")
    with pytest.raises(ScienceError):
        pay_science_worker(job["job_id"])


def test_science_security_limits_are_enforced(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-limits.sqlite3")
    monkeypatch.setattr("app.services.science.SCIENCE_MAX_REWARD_PER_JOB", 5)
    monkeypatch.setattr("app.services.science.SCIENCE_MAX_PENDING_PER_REQUESTER", 8)
    monkeypatch.setattr("app.services.science.SCIENCE_MAX_PAYOUT_PER_EPOCH", 3)
    monkeypatch.setattr("app.services.science.SCIENCE_RESERVE_GOVERNANCE_TIMELOCK_SECONDS", 0)
    _seed_science_reserve(100)
    _activate_science_reserve()
    stake_science_access("addr-limits", 31_416)

    with pytest.raises(ScienceError):
        create_science_job("addr-limits", "ai", "meta", "ipfs://x", max_compute_units=10, reward_per_compute_unit=1, max_reward=6)

    create_science_job("addr-limits", "ai", "meta", "ipfs://x", max_compute_units=4, reward_per_compute_unit=1, max_reward=4)
    with pytest.raises(ScienceError):
        create_science_job("addr-limits", "ai", "meta", "ipfs://y", max_compute_units=5, reward_per_compute_unit=1, max_reward=5)

    stake_science_access("addr-epoch", 31_416)
    job = _accepted_job("addr-epoch", "worker-epoch", compute_units_used=4)
    with pytest.raises(ScienceError):
        pay_science_worker(job["job_id"])


def test_science_terminal_and_incomplete_jobs_do_not_pay(tmp_path, monkeypatch) -> None:
    _init_science_db(tmp_path, monkeypatch, "science-terminal.sqlite3")
    monkeypatch.setattr("app.services.science.SCIENCE_RESERVE_GOVERNANCE_TIMELOCK_SECONDS", 0)
    _seed_science_reserve(100)
    _activate_science_reserve()
    stake_science_access("addr-terminal", 31_416)

    for status in ("disputed", "expired"):
        job = create_science_job("addr-terminal", status, f"meta-{status}", f"ipfs://{status}", max_compute_units=1, reward_per_compute_unit=1, max_reward=1)
        if status == "disputed":
            transition_science_job(job["job_id"], "queued")
        transition_science_job(job["job_id"], status)
        with pytest.raises(ScienceError):
            pay_science_worker(job["job_id"])

    submitted = create_science_job("addr-terminal", "submitted", "meta-submitted", "ipfs://submitted", max_compute_units=1, reward_per_compute_unit=1, max_reward=1)
    transition_science_job(submitted["job_id"], "queued")
    transition_science_job(submitted["job_id"], "assigned", worker_address="worker-submitted")
    transition_science_job(submitted["job_id"], "committed", worker_address="worker-submitted")
    transition_science_job(
        submitted["job_id"],
        "submitted",
        worker_address="worker-submitted",
        result_hash="result_hash",
        proof_hash="proof_hash",
    )
    with pytest.raises(ScienceError):
        pay_science_worker(submitted["job_id"])


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


def _accepted_job(requester: str, worker: str, compute_units_used: float) -> dict:
    job = create_science_job(
        requester,
        "ai_inference",
        f"metadata_hash_{worker}",
        f"ipfs://payload-{worker}",
        max_compute_units=10,
        reward_per_compute_unit=1,
        max_reward=5,
    )
    transition_science_job(job["job_id"], "queued")
    transition_science_job(job["job_id"], "assigned", worker_address=worker)
    transition_science_job(job["job_id"], "committed", worker_address=worker)
    transition_science_job(
        job["job_id"],
        "submitted",
        worker_address=worker,
        result_hash="result_hash",
        proof_hash="proof_hash",
    )
    transition_science_job(job["job_id"], "verified")
    return transition_science_job(job["job_id"], "accepted", compute_units_used=compute_units_used)


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
