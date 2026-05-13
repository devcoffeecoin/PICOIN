import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.settings import (
    SCIENCE_ALLOW_SELF_WORK,
    SCIENCE_BASE_MONTHLY_QUOTA_UNITS,
    SCIENCE_COMPUTE_REWARD_PERCENT_OF_BLOCK,
    SCIENCE_MAX_PAYOUT_PER_EPOCH,
    SCIENCE_MAX_PENDING_PER_REQUESTER,
    SCIENCE_MAX_REWARD_PER_JOB,
    SCIENCE_RESERVE_ACTIVE_STATUS,
    SCIENCE_RESERVE_ACCOUNT_ID,
    SCIENCE_RESERVE_AUTHORIZED_SIGNERS,
    SCIENCE_RESERVE_GOVERNANCE_MULTISIG_THRESHOLD,
    SCIENCE_RESERVE_GOVERNANCE_TIMELOCK_SECONDS,
    SCIENCE_RESERVE_LOCKED_STATUS,
    SCIENCE_RESERVE_PAUSED_STATUS,
    SCIENCE_RESERVE_PENDING_STATUS,
)
from app.db.database import get_connection, row_to_dict


SCIENCE_TIERS = {
    "researcher": {"stake_required": 3_141.6, "compute_multiplier": 1, "priority": "low"},
    "lab": {"stake_required": 31_416.0, "compute_multiplier": 10, "priority": "medium"},
    "institution": {"stake_required": 314_160.0, "compute_multiplier": 100, "priority": "high"},
}
ACTIVE_JOB_STATUSES = {"created", "queued", "assigned", "committed", "submitted", "verified", "accepted"}
TERMINAL_JOB_STATUSES = {"rejected", "disputed", "expired", "paid"}
SCIENCE_EVENT_TITLES = {
    "ScienceStakeUpdated": "Science stake actualizado",
    "ScienceJobCreated": "Science job creado",
    "ScienceJobAssigned": "Science job asignado",
    "ScienceJobCommitted": "Science job committed",
    "ScienceJobSubmitted": "Science job submitted",
    "ScienceJobVerified": "Science job verified",
    "ScienceJobAccepted": "Science job aceptado",
    "ScienceJobRejected": "Science job rechazado",
    "ScienceJobPaid": "Science job pagado",
    "ScienceJobDisputed": "Science job disputado",
    "ScienceReserveAccrued": "Science reserve acumulada",
    "ScienceReserveActivationProposed": "Science reserve activation proposed",
    "ScienceReserveActivationApproved": "Science reserve activation approved",
    "ScienceReserveActivated": "Science reserve activated",
    "ScienceReserveLocked": "Science reserve locked",
    "ScienceReserveUnlocked": "Science reserve unlocked",
    "ScienceReservePaused": "Science reserve paused",
    "ScienceReserveUnpaused": "Science reserve unpaused",
    "ScientificTreasuryClaimed": "Scientific treasury claimed",
}


class ScienceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def current_epoch() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def determine_science_tier(stake_amount: float) -> dict[str, Any] | None:
    amount = float(stake_amount)
    if amount >= SCIENCE_TIERS["institution"]["stake_required"]:
        return {"tier": "institution", **SCIENCE_TIERS["institution"]}
    if amount >= SCIENCE_TIERS["lab"]["stake_required"]:
        return {"tier": "lab", **SCIENCE_TIERS["lab"]}
    if amount >= SCIENCE_TIERS["researcher"]["stake_required"]:
        return {"tier": "researcher", **SCIENCE_TIERS["researcher"]}
    return None


def stake_science_access(address: str, amount: float) -> dict[str, Any]:
    with get_connection() as connection:
        return stake_science_access_in_connection(connection, address, amount)


def stake_science_access_in_connection(
    connection: Any,
    address: str,
    amount: float,
    *,
    account_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    address = _clean_address(address)
    stake_amount = round(float(amount), 8)
    if stake_amount <= 0:
        raise ScienceError(400, "stake amount must be positive")
    tier = determine_science_tier(stake_amount)
    if tier is None:
        raise ScienceError(400, "stake amount is below researcher minimum")

    now = timestamp or utc_now()
    existing = row_to_dict(
        connection.execute(
            "SELECT * FROM science_stake_accounts WHERE address = ?",
            (address,),
        ).fetchone()
    )
    account_id = existing["account_id"] if existing else (account_id or f"science_{uuid.uuid4().hex[:16]}")
    epoch = existing["monthly_quota_epoch"] if existing else current_epoch()
    quota_used = float(existing["monthly_quota_used"]) if existing else 0.0
    if epoch != current_epoch():
        epoch = current_epoch()
        quota_used = 0.0
    connection.execute(
        """
        INSERT INTO science_stake_accounts (
            account_id, address, stake_amount, tier, compute_multiplier,
            monthly_quota_used, monthly_quota_epoch, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        ON CONFLICT(address) DO UPDATE SET
            stake_amount = excluded.stake_amount,
            tier = excluded.tier,
            compute_multiplier = excluded.compute_multiplier,
            monthly_quota_used = excluded.monthly_quota_used,
            monthly_quota_epoch = excluded.monthly_quota_epoch,
            status = 'active',
            updated_at = excluded.updated_at
        """,
        (
            account_id,
            address,
            stake_amount,
            tier["tier"],
            tier["compute_multiplier"],
            quota_used,
            epoch,
            now,
            now,
        ),
    )
    _record_science_event(
        connection,
        "ScienceStakeUpdated",
        address=address,
        payload={
            "stake_amount": stake_amount,
            "tier": tier["tier"],
            "compute_multiplier": tier["compute_multiplier"],
            "priority": tier["priority"],
        },
    )
    account = _science_account_by_address(connection, address)
    return _enrich_science_account(account)


def get_science_account(address: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        account = _science_account_by_address(connection, _clean_address(address))
    return _enrich_science_account(account)


def list_science_accounts(limit: int = 100) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM science_stake_accounts
            ORDER BY stake_amount DESC, address ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_enrich_science_account(row_to_dict(row)) for row in rows]


def unstake_science_access(address: str) -> dict[str, Any]:
    with get_connection() as connection:
        return unstake_science_access_in_connection(connection, address)


def unstake_science_access_in_connection(
    connection: Any,
    address: str,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    address = _clean_address(address)
    account = _science_account_by_address(connection, address)
    if account is None:
        raise ScienceError(404, "science stake account not found")
    active_jobs = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM science_jobs
        WHERE requester_address = ?
        AND status IN ('created', 'queued', 'assigned', 'committed', 'submitted', 'verified', 'accepted')
        AND paid = 0
        """,
        (address,),
    ).fetchone()["count"]
    if int(active_jobs) > 0:
        raise ScienceError(409, "cannot unstake while science jobs are active")
    now = timestamp or utc_now()
    connection.execute(
        """
        UPDATE science_stake_accounts
        SET stake_amount = 0,
            tier = NULL,
            compute_multiplier = 0,
            status = 'unstaking',
            updated_at = ?
        WHERE address = ?
        """,
        (now, address),
    )
    _record_science_event(
        connection,
        "ScienceStakeUpdated",
        address=address,
        payload={"stake_amount": 0.0, "tier": None, "status": "unstaking"},
    )
    updated = _science_account_by_address(connection, address)
    return _enrich_science_account(updated)


def create_science_job(
    requester_address: str,
    job_type: str,
    metadata_hash: str,
    storage_pointer: str,
    reward_budget: float | None = None,
    max_compute_units: float | None = None,
    reward_per_compute_unit: float | None = None,
    max_reward: float | None = None,
) -> dict[str, Any]:
    with get_connection() as connection:
        return create_science_job_in_connection(
            connection,
            requester_address,
            job_type,
            metadata_hash,
            storage_pointer,
            reward_budget=reward_budget,
            max_compute_units=max_compute_units,
            reward_per_compute_unit=reward_per_compute_unit,
            max_reward=max_reward,
        )


def create_science_job_in_connection(
    connection: Any,
    requester_address: str,
    job_type: str,
    metadata_hash: str,
    storage_pointer: str,
    reward_budget: float | None = None,
    max_compute_units: float | None = None,
    reward_per_compute_unit: float | None = None,
    max_reward: float | None = None,
    *,
    job_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    requester_address = _clean_address(requester_address)
    job_type = _clean_text(job_type, "job_type")
    metadata_hash = _clean_text(metadata_hash, "metadata_hash")
    storage_pointer = _clean_text(storage_pointer, "storage_pointer")
    compute_units, reward_per_unit, reward_cap = _normalize_compute_budget(
        reward_budget=reward_budget,
        max_compute_units=max_compute_units,
        reward_per_compute_unit=reward_per_compute_unit,
        max_reward=max_reward,
    )

    account = _require_active_science_account(connection, requester_address)
    account = _reset_quota_if_needed(connection, account)
    quota_limit = _monthly_quota_limit(account)
    if float(account["monthly_quota_used"]) + 1 > quota_limit:
        raise ScienceError(429, "monthly science quota exceeded")
    if reward_cap > 0:
        _validate_requester_pending_limit(connection, requester_address, reward_cap)
        _reserve_science_job_budget(connection, reward_cap)

    job_id = job_id or f"science_job_{uuid.uuid4().hex[:16]}"
    now = timestamp or utc_now()
    connection.execute(
        """
        INSERT INTO science_jobs (
            job_id, requester_address, tier_at_creation, job_type, metadata_hash,
            storage_pointer, reward_budget, max_compute_units,
            reward_per_compute_unit, max_reward, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)
        """,
        (
            job_id,
            requester_address,
            account["tier"],
            job_type,
            metadata_hash,
            storage_pointer,
            reward_cap,
            compute_units,
            reward_per_unit,
            reward_cap,
            now,
            now,
        ),
    )
    connection.execute(
        """
        UPDATE science_stake_accounts
        SET monthly_quota_used = monthly_quota_used + 1,
            updated_at = ?
        WHERE address = ?
        """,
        (now, requester_address),
    )
    _record_science_event(
        connection,
        "ScienceJobCreated",
        address=requester_address,
        job_id=job_id,
        payload={
            "tier": account["tier"],
            "job_type": job_type,
            "metadata_hash": metadata_hash,
            "storage_pointer": storage_pointer,
            "max_compute_units": compute_units,
            "reward_per_compute_unit": reward_per_unit,
            "max_reward": reward_cap,
        },
    )
    job = _science_job_by_id(connection, job_id)
    return _decode_science_job(job)


def list_science_jobs(address: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with get_connection() as connection:
        if address:
            rows = connection.execute(
                """
                SELECT *
                FROM science_jobs
                WHERE requester_address = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (_clean_address(address), limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT *
                FROM science_jobs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [_decode_science_job(row_to_dict(row)) for row in rows]


def get_science_job(job_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        job = _science_job_by_id(connection, job_id)
    return _decode_science_job(job)


def transition_science_job(
    job_id: str,
    status: str,
    worker_address: str | None = None,
    result_hash: str | None = None,
    proof_hash: str | None = None,
    compute_units_used: float | None = None,
) -> dict[str, Any]:
    status = status.strip().lower()
    with get_connection() as connection:
        job = _science_job_by_id(connection, job_id)
        if job is None:
            raise ScienceError(404, "science job not found")
        _validate_science_transition(job, status, worker_address, result_hash, proof_hash, compute_units_used)

        next_worker = _clean_address(worker_address) if worker_address else job.get("worker_address")
        next_result = _clean_optional_hash(result_hash, "result_hash") or job.get("result_hash")
        next_proof = _clean_optional_hash(proof_hash, "proof_hash") or job.get("proof_hash")
        next_compute_units = _compute_units_for_status(job, status, compute_units_used)
        next_payout = _payout_amount(job, next_compute_units) if status == "accepted" else float(job.get("payout_amount") or 0)
        now = utc_now()
        connection.execute(
            """
            UPDATE science_jobs
            SET status = ?,
                worker_address = ?,
                result_hash = ?,
                proof_hash = ?,
                compute_units_used = ?,
                payout_amount = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (status, next_worker, next_result, next_proof, next_compute_units, next_payout, now, job_id),
        )
        updated = _science_job_by_id(connection, job_id)
        _record_science_event(
            connection,
            _event_type_for_status(status),
            address=updated["requester_address"],
            job_id=job_id,
            payload={
                "status": status,
                "worker_address": next_worker,
                "result_hash": next_result,
                "proof_hash": next_proof,
                "compute_units_used": next_compute_units,
                "payout_amount": next_payout,
            },
        )
        if status in TERMINAL_JOB_STATUSES:
            _release_science_job_budget(connection, updated)
            updated = _science_job_by_id(connection, job_id)
    return _decode_science_job(updated)


def pay_science_worker(job_id: str) -> dict[str, Any]:
    with get_connection() as connection:
        _require_science_payouts_enabled(connection)
        job = _science_job_by_id(connection, job_id)
        if job is None:
            raise ScienceError(404, "science job not found")
        if job["status"] != "accepted":
            raise ScienceError(409, "science job must be accepted before payment")
        if int(job["paid"]):
            raise ScienceError(409, "science job has already been paid")
        if not job.get("worker_address"):
            raise ScienceError(409, "science job has no worker")
        amount = round(float(job["payout_amount"]), 8)
        if amount <= 0:
            raise ScienceError(409, "science job has no payable compute units")
        _validate_epoch_payout_limit(connection, amount)

        _apply_ledger_entry(
            connection,
            account_id=SCIENCE_RESERVE_ACCOUNT_ID,
            account_type="science_reserve",
            amount=-amount,
            entry_type="science_worker_payment",
            related_id=job_id,
            description="science worker payment debit",
        )
        _apply_ledger_entry(
            connection,
            account_id=job["worker_address"],
            account_type="science_worker",
            amount=amount,
            entry_type="science_worker_payment",
            related_id=job_id,
            description="science worker payment",
        )
        _update_reserve_paid(connection, amount)
        now = utc_now()
        connection.execute(
            """
            UPDATE science_jobs
            SET status = 'paid',
                paid = 1,
                paid_amount = ?,
                paid_at = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (amount, now, now, job_id),
        )
        _record_science_event(
            connection,
            "ScienceJobPaid",
            address=job["worker_address"],
            job_id=job_id,
            payload={
                "amount": amount,
                "worker_address": job["worker_address"],
                "compute_units_used": job["compute_units_used"],
                "reward_per_compute_unit": job["reward_per_compute_unit"],
                "max_reward": job["max_reward"],
            },
        )
        paid_job = _science_job_by_id(connection, job_id)
    return _decode_science_job(paid_job)


def get_science_reserve(epoch: str | None = None) -> dict[str, Any]:
    with get_connection() as connection:
        reserve = _reserve_row(connection, epoch or current_epoch())
        governance = _science_reserve_governance(connection)
    return _decode_reserve(reserve, governance)


def propose_science_reserve_activation(signer: str) -> dict[str, Any]:
    with get_connection() as connection:
        return propose_science_reserve_activation_in_connection(connection, signer)


def propose_science_reserve_activation_in_connection(
    connection: Any,
    signer: str,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    signer = _clean_address(signer)
    governance = _science_reserve_governance(connection)
    if governance["status"] == SCIENCE_RESERVE_ACTIVE_STATUS:
        raise ScienceError(409, "science reserve is already active")
    _require_authorized_signer(governance, signer)
    approvals = _governance_approvals(governance)
    if signer not in approvals:
        approvals.append(signer)
    now = _parse_iso(timestamp) if timestamp else utc_now_dt()
    available_at = now + timedelta(seconds=SCIENCE_RESERVE_GOVERNANCE_TIMELOCK_SECONDS)
    connection.execute(
        """
        UPDATE science_reserve_governance
        SET status = ?,
            activation_requested_at = ?,
            activation_available_at = ?,
            approvals = ?,
            payouts_enabled = 0,
            updated_at = ?
        WHERE id = 1
        """,
        (
            SCIENCE_RESERVE_PENDING_STATUS,
            now.isoformat(),
            available_at.isoformat(),
            json.dumps(approvals),
            now.isoformat(),
        ),
    )
    _record_science_event(
        connection,
        "ScienceReserveActivationProposed",
        address=signer,
        payload={
            "status": SCIENCE_RESERVE_PENDING_STATUS,
            "activation_available_at": available_at.isoformat(),
            "approvals": approvals,
            "threshold": SCIENCE_RESERVE_GOVERNANCE_MULTISIG_THRESHOLD,
        },
    )
    governance = _science_reserve_governance(connection)
    return _decode_governance(governance)


def approve_science_reserve_activation(signer: str) -> dict[str, Any]:
    with get_connection() as connection:
        return approve_science_reserve_activation_in_connection(connection, signer)


def approve_science_reserve_activation_in_connection(
    connection: Any,
    signer: str,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    signer = _clean_address(signer)
    governance = _science_reserve_governance(connection)
    if governance["status"] == SCIENCE_RESERVE_ACTIVE_STATUS:
        raise ScienceError(409, "science reserve is already active")
    if not governance["activation_requested_at"]:
        raise ScienceError(409, "science reserve activation has not been proposed")
    _require_authorized_signer(governance, signer)
    approvals = _governance_approvals(governance)
    if signer not in approvals:
        approvals.append(signer)
    connection.execute(
        """
        UPDATE science_reserve_governance
        SET approvals = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (json.dumps(approvals), timestamp or utc_now()),
    )
    _record_science_event(
        connection,
        "ScienceReserveActivationApproved",
        address=signer,
        payload={
            "approvals": approvals,
            "threshold": SCIENCE_RESERVE_GOVERNANCE_MULTISIG_THRESHOLD,
        },
    )
    governance = _science_reserve_governance(connection)
    return _decode_governance(governance)


def execute_science_reserve_activation() -> dict[str, Any]:
    with get_connection() as connection:
        return execute_science_reserve_activation_in_connection(connection)


def execute_science_reserve_activation_in_connection(
    connection: Any,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    governance = _science_reserve_governance(connection)
    if governance["status"] == SCIENCE_RESERVE_ACTIVE_STATUS:
        return _decode_governance(governance)
    if not governance["activation_available_at"]:
        raise ScienceError(409, "science reserve activation has not been proposed")
    approvals = _governance_approvals(governance)
    if len(approvals) < SCIENCE_RESERVE_GOVERNANCE_MULTISIG_THRESHOLD:
        raise ScienceError(409, "science reserve activation lacks multisig approvals")
    available_at = _parse_iso(governance["activation_available_at"])
    now_dt = _parse_iso(timestamp) if timestamp else utc_now_dt()
    if now_dt < available_at:
        raise ScienceError(423, f"science reserve activation timelock active until {governance['activation_available_at']}")
    now = now_dt.isoformat()
    connection.execute(
        """
        UPDATE science_reserve_governance
        SET status = ?,
            activated_at = ?,
            payouts_enabled = 1,
            emergency_paused = 0,
            updated_at = ?
        WHERE id = 1
        """,
        (SCIENCE_RESERVE_ACTIVE_STATUS, now, now),
    )
    _record_science_event(
        connection,
        "ScienceReserveActivated",
        payload={"status": SCIENCE_RESERVE_ACTIVE_STATUS, "approvals": approvals},
    )
    _record_science_event(
        connection,
        "ScienceReserveUnlocked",
        payload={"status": SCIENCE_RESERVE_ACTIVE_STATUS, "payouts_enabled": True},
    )
    governance = _science_reserve_governance(connection)
    return _decode_governance(governance)


def pause_science_reserve(signer: str) -> dict[str, Any]:
    with get_connection() as connection:
        return pause_science_reserve_in_connection(connection, signer)


def pause_science_reserve_in_connection(
    connection: Any,
    signer: str,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    signer = _clean_address(signer)
    governance = _science_reserve_governance(connection)
    _require_authorized_signer(governance, signer)
    now = timestamp or utc_now()
    connection.execute(
        """
        UPDATE science_reserve_governance
        SET status = ?,
            emergency_paused = 1,
            payouts_enabled = 0,
            updated_at = ?
        WHERE id = 1
        """,
        (SCIENCE_RESERVE_PAUSED_STATUS, now),
    )
    _record_science_event(
        connection,
        "ScienceReservePaused",
        address=signer,
        payload={"status": SCIENCE_RESERVE_PAUSED_STATUS},
    )
    governance = _science_reserve_governance(connection)
    return _decode_governance(governance)


def unpause_science_reserve(signer: str) -> dict[str, Any]:
    with get_connection() as connection:
        return unpause_science_reserve_in_connection(connection, signer)


def unpause_science_reserve_in_connection(
    connection: Any,
    signer: str,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    signer = _clean_address(signer)
    governance = _science_reserve_governance(connection)
    _require_authorized_signer(governance, signer)
    if not governance["activated_at"]:
        next_status = SCIENCE_RESERVE_LOCKED_STATUS
        payouts_enabled = 0
    else:
        next_status = SCIENCE_RESERVE_ACTIVE_STATUS
        payouts_enabled = 1
    now = timestamp or utc_now()
    connection.execute(
        """
        UPDATE science_reserve_governance
        SET status = ?,
            emergency_paused = 0,
            payouts_enabled = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (next_status, payouts_enabled, now),
    )
    _record_science_event(
        connection,
        "ScienceReserveUnpaused",
        address=signer,
        payload={"status": next_status, "payouts_enabled": bool(payouts_enabled)},
    )
    governance = _science_reserve_governance(connection)
    return _decode_governance(governance)


def get_science_reserve_governance() -> dict[str, Any]:
    with get_connection() as connection:
        governance = _science_reserve_governance(connection)
    return _decode_governance(governance)


def record_science_reserve_for_block(connection: Any, block_height: int, block_reward: float) -> float:
    amount = round(float(block_reward) * SCIENCE_COMPUTE_REWARD_PERCENT_OF_BLOCK, 8)
    if amount <= 0:
        return 0.0
    epoch = current_epoch()
    _ensure_reserve_epoch(connection, epoch)
    connection.execute(
        """
        UPDATE science_reward_reserve
        SET total_reserved = total_reserved + ?,
            updated_at = ?
        WHERE epoch = ?
        """,
        (amount, utc_now(), epoch),
    )
    _apply_ledger_entry(
        connection,
        account_id=SCIENCE_RESERVE_ACCOUNT_ID,
        account_type="science_reserve",
        amount=amount,
        entry_type="science_reserve_accrual",
        block_height=block_height,
        related_id=f"block:{block_height}",
        description="science compute reserve accrual",
    )
    _record_science_event(
        connection,
        "ScienceReserveAccrued",
        payload={"block_height": block_height, "amount": amount, "epoch": epoch},
    )
    return amount


def get_science_events(limit: int = 20) -> list[dict[str, Any]]:
    with get_connection() as connection:
        return _science_events_from_connection(connection, limit)


def science_events_for_node(connection: Any, limit: int) -> list[dict[str, Any]]:
    return _science_events_from_connection(connection, limit)


def _science_events_from_connection(connection: Any, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM science_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    events = []
    for row in rows:
        payload = json.loads(row["payload"])
        event_type = row["event_type"]
        events.append(
            {
                "id": row["id"],
                "type": event_type,
                "title": SCIENCE_EVENT_TITLES.get(event_type, event_type),
                "message": _science_event_message(event_type, row["address"], row["job_id"], payload),
                "severity": "bad" if event_type in {"ScienceJobRejected", "ScienceJobDisputed"} else "info",
                "created_at": row["created_at"],
                "related_id": row["job_id"],
                "block_height": payload.get("block_height"),
                "actor_id": row["address"],
                "metadata": payload,
            }
        )
    return events


def _validate_science_transition(
    job: dict[str, Any],
    status: str,
    worker_address: str | None,
    result_hash: str | None,
    proof_hash: str | None,
    compute_units_used: float | None = None,
) -> None:
    current = job["status"]
    allowed = {
        "created": {"queued", "rejected", "expired"},
        "queued": {"assigned", "rejected", "expired", "disputed"},
        "assigned": {"committed", "rejected", "expired", "disputed"},
        "committed": {"submitted", "rejected", "expired", "disputed"},
        "submitted": {"verified", "rejected", "disputed"},
        "verified": {"accepted", "rejected", "disputed"},
        "accepted": set(),
        "paid": set(),
        "rejected": set(),
        "disputed": set(),
        "expired": set(),
    }
    if status not in allowed.get(current, set()):
        raise ScienceError(409, f"invalid science job transition {current} -> {status}")
    if status in {"assigned", "committed", "submitted", "verified", "accepted"}:
        candidate_worker = worker_address or job.get("worker_address")
        if not candidate_worker:
            raise ScienceError(400, "worker_address is required for this transition")
        if not SCIENCE_ALLOW_SELF_WORK and _clean_address(candidate_worker) == job["requester_address"]:
            raise ScienceError(403, "requester cannot be paid as worker for its own job")
    if status in {"submitted", "verified", "accepted"}:
        if not (result_hash or job.get("result_hash")):
            raise ScienceError(400, "result_hash is required for submitted or later jobs")
        if not (proof_hash or job.get("proof_hash")):
            raise ScienceError(400, "proof_hash is required for submitted or later jobs")
    if status == "accepted":
        units = _clean_compute_units(compute_units_used, "compute_units_used")
        if units <= 0:
            raise ScienceError(400, "compute_units_used must be positive when accepting a science job")
        if units > float(job["max_compute_units"]):
            raise ScienceError(400, "compute_units_used cannot exceed max_compute_units")


def _event_type_for_status(status: str) -> str:
    return {
        "assigned": "ScienceJobAssigned",
        "committed": "ScienceJobCommitted",
        "submitted": "ScienceJobSubmitted",
        "verified": "ScienceJobVerified",
        "accepted": "ScienceJobAccepted",
        "rejected": "ScienceJobRejected",
        "disputed": "ScienceJobDisputed",
        "expired": "ScienceJobRejected",
        "paid": "ScienceJobPaid",
        "queued": "ScienceJobCreated",
    }[status]


def _require_active_science_account(connection: Any, address: str) -> dict[str, Any]:
    account = _science_account_by_address(connection, address)
    if account is None:
        raise ScienceError(403, "science stake account is required")
    account = _reset_quota_if_needed(connection, account)
    if account["status"] != "active":
        raise ScienceError(403, "science stake account is not active")
    tier = determine_science_tier(float(account["stake_amount"]))
    if tier is None or account["tier"] is None:
        raise ScienceError(403, "science stake is below active tier minimum")
    return account


def _reset_quota_if_needed(connection: Any, account: dict[str, Any]) -> dict[str, Any]:
    epoch = current_epoch()
    if account["monthly_quota_epoch"] == epoch:
        return account
    connection.execute(
        """
        UPDATE science_stake_accounts
        SET monthly_quota_used = 0,
            monthly_quota_epoch = ?,
            updated_at = ?
        WHERE address = ?
        """,
        (epoch, utc_now(), account["address"]),
    )
    return _science_account_by_address(connection, account["address"])


def _normalize_compute_budget(
    *,
    reward_budget: float | None,
    max_compute_units: float | None,
    reward_per_compute_unit: float | None,
    max_reward: float | None,
) -> tuple[float, float, float]:
    legacy_budget = _clean_non_negative(reward_budget, "reward_budget") if reward_budget is not None else None
    if max_reward is None and legacy_budget is not None:
        max_reward = legacy_budget
    reward_cap = _clean_non_negative(max_reward or 0, "max_reward")
    default_units = 1 if reward_cap > 0 and max_compute_units is None else 0
    compute_units = _clean_non_negative(
        max_compute_units if max_compute_units is not None else default_units,
        "max_compute_units",
    )
    default_reward_per_unit = reward_cap if reward_cap > 0 and compute_units == 1 and reward_per_compute_unit is None else 0
    reward_per_unit = _clean_non_negative(
        reward_per_compute_unit if reward_per_compute_unit is not None else default_reward_per_unit,
        "reward_per_compute_unit",
    )
    if reward_cap > SCIENCE_MAX_REWARD_PER_JOB:
        raise ScienceError(400, "max_reward exceeds protocol max_reward_per_job")
    if reward_cap > 0 and (compute_units <= 0 or reward_per_unit <= 0):
        raise ScienceError(400, "max_compute_units and reward_per_compute_unit are required for paid science jobs")
    computed_cap = round(compute_units * reward_per_unit, 8)
    if reward_cap > computed_cap:
        raise ScienceError(400, "max_reward cannot exceed max_compute_units * reward_per_compute_unit")
    return compute_units, reward_per_unit, reward_cap


def _validate_requester_pending_limit(connection: Any, requester_address: str, additional_max_reward: float) -> None:
    row = connection.execute(
        """
        SELECT COALESCE(SUM(max_reward), 0) AS pending
        FROM science_jobs
        WHERE requester_address = ?
        AND paid = 0
        AND status IN ('created', 'queued', 'assigned', 'committed', 'submitted', 'verified', 'accepted')
        """,
        (requester_address,),
    ).fetchone()
    pending = round(float(row["pending"]) + additional_max_reward, 8)
    if pending > SCIENCE_MAX_PENDING_PER_REQUESTER:
        raise ScienceError(429, "requester science pending rewards exceed max_pending_per_requester")


def _compute_units_for_status(job: dict[str, Any], status: str, compute_units_used: float | None) -> float:
    if status == "accepted":
        return _clean_compute_units(compute_units_used, "compute_units_used")
    return round(float(job.get("compute_units_used") or 0), 8)


def _payout_amount(job: dict[str, Any], compute_units_used: float) -> float:
    return round(
        min(
            compute_units_used * float(job["reward_per_compute_unit"]),
            float(job["max_reward"]),
        ),
        8,
    )


def _reserve_science_job_budget(connection: Any, amount: float) -> None:
    epoch = current_epoch()
    reserve = _ensure_reserve_epoch(connection, epoch)
    available = round(float(reserve["total_reserved"]) - float(reserve["total_paid"]) - float(reserve["total_pending"]), 8)
    if amount > available:
        raise ScienceError(409, "science compute reserve is insufficient for reward budget")
    connection.execute(
        """
        UPDATE science_reward_reserve
        SET total_pending = total_pending + ?,
            updated_at = ?
        WHERE epoch = ?
        """,
        (amount, utc_now(), epoch),
    )


def _release_science_job_budget(connection: Any, job: dict[str, Any]) -> None:
    amount = round(float(job["max_reward"]), 8)
    if amount <= 0 or int(job["paid"]):
        return
    epoch = current_epoch()
    _ensure_reserve_epoch(connection, epoch)
    connection.execute(
        """
        UPDATE science_reward_reserve
        SET total_pending = MAX(0, total_pending - ?),
            updated_at = ?
        WHERE epoch = ?
        """,
        (amount, utc_now(), epoch),
    )


def _update_reserve_paid(connection: Any, amount: float) -> None:
    epoch = current_epoch()
    _ensure_reserve_epoch(connection, epoch)
    connection.execute(
        """
        UPDATE science_reward_reserve
        SET total_paid = total_paid + ?,
            total_pending = MAX(0, total_pending - ?),
            updated_at = ?
        WHERE epoch = ?
        """,
        (amount, amount, utc_now(), epoch),
    )


def _ensure_reserve_epoch(connection: Any, epoch: str) -> dict[str, Any]:
    now = utc_now()
    connection.execute(
        """
        INSERT INTO science_reward_reserve (epoch, total_reserved, total_paid, total_pending, updated_at)
        VALUES (?, 0, 0, 0, ?)
        ON CONFLICT(epoch) DO NOTHING
        """,
        (epoch, now),
    )
    return _reserve_row(connection, epoch)


def _reserve_row(connection: Any, epoch: str) -> dict[str, Any]:
    row = row_to_dict(
        connection.execute(
            "SELECT * FROM science_reward_reserve WHERE epoch = ?",
            (epoch,),
        ).fetchone()
    )
    if row is None:
        row = _ensure_reserve_epoch(connection, epoch)
    return row


def _science_account_by_address(connection: Any, address: str) -> dict[str, Any] | None:
    return row_to_dict(
        connection.execute(
            "SELECT * FROM science_stake_accounts WHERE address = ?",
            (address,),
        ).fetchone()
    )


def _science_job_by_id(connection: Any, job_id: str) -> dict[str, Any] | None:
    return row_to_dict(
        connection.execute(
            "SELECT * FROM science_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    )


def _enrich_science_account(account: dict[str, Any] | None) -> dict[str, Any] | None:
    if account is None:
        return None
    account["monthly_quota_limit"] = _monthly_quota_limit(account)
    account["priority"] = SCIENCE_TIERS.get(account.get("tier"), {}).get("priority")
    return account


def _monthly_quota_limit(account: dict[str, Any]) -> float:
    return float(SCIENCE_BASE_MONTHLY_QUOTA_UNITS * int(account["compute_multiplier"]))


def _decode_science_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if job is None:
        return None
    job["paid"] = bool(job["paid"])
    job["reward_budget"] = float(job.get("max_reward") or job.get("reward_budget") or 0)
    return job


def _decode_reserve(reserve: dict[str, Any], governance: dict[str, Any] | None = None) -> dict[str, Any]:
    reserve = dict(reserve)
    governance = governance or {}
    reserve["available"] = round(
        float(reserve["total_reserved"]) - float(reserve["total_paid"]) - float(reserve["total_pending"]),
        8,
    )
    reserve["status"] = governance.get("status", SCIENCE_RESERVE_LOCKED_STATUS)
    reserve["activation_requested_at"] = governance.get("activation_requested_at")
    reserve["activation_available_at"] = governance.get("activation_available_at")
    reserve["activated_at"] = governance.get("activated_at")
    reserve["governance_approvals"] = _governance_approvals(governance) if governance else []
    reserve["authorized_signers"] = _authorized_signers(governance) if governance else list(SCIENCE_RESERVE_AUTHORIZED_SIGNERS)
    reserve["governance_threshold"] = SCIENCE_RESERVE_GOVERNANCE_MULTISIG_THRESHOLD
    reserve["payouts_enabled"] = bool(governance.get("payouts_enabled", 0))
    reserve["emergency_paused"] = bool(governance.get("emergency_paused", 0))
    reserve["max_reward_per_job"] = SCIENCE_MAX_REWARD_PER_JOB
    reserve["max_payout_per_epoch"] = SCIENCE_MAX_PAYOUT_PER_EPOCH
    reserve["max_pending_per_requester"] = SCIENCE_MAX_PENDING_PER_REQUESTER
    return reserve


def _decode_governance(governance: dict[str, Any]) -> dict[str, Any]:
    governance = dict(governance)
    governance["approvals"] = _governance_approvals(governance)
    governance["authorized_signers"] = _authorized_signers(governance)
    governance["payouts_enabled"] = bool(governance.get("payouts_enabled", 0))
    governance["emergency_paused"] = bool(governance.get("emergency_paused", 0))
    governance["threshold"] = SCIENCE_RESERVE_GOVERNANCE_MULTISIG_THRESHOLD
    governance["timelock_seconds"] = SCIENCE_RESERVE_GOVERNANCE_TIMELOCK_SECONDS
    return governance


def _science_reserve_governance(connection: Any) -> dict[str, Any]:
    row = row_to_dict(connection.execute("SELECT * FROM science_reserve_governance WHERE id = 1").fetchone())
    if row is not None:
        return row
    now = utc_now()
    connection.execute(
        """
        INSERT INTO science_reserve_governance (
            id, status, activation_requested_at, activation_available_at,
            activated_at, approvals, authorized_signers, payouts_enabled,
            emergency_paused, updated_at
        )
        VALUES (1, ?, NULL, NULL, NULL, '[]', ?, 0, 0, ?)
        """,
        (SCIENCE_RESERVE_LOCKED_STATUS, json.dumps(list(SCIENCE_RESERVE_AUTHORIZED_SIGNERS)), now),
    )
    return row_to_dict(connection.execute("SELECT * FROM science_reserve_governance WHERE id = 1").fetchone())


def _require_science_reserve_active(connection: Any) -> None:
    governance = _science_reserve_governance(connection)
    if governance["status"] != SCIENCE_RESERVE_ACTIVE_STATUS:
        raise ScienceError(423, "science compute reserve is locked until L2 marketplace activation")


def _require_science_payouts_enabled(connection: Any) -> None:
    governance = _science_reserve_governance(connection)
    if governance["status"] != SCIENCE_RESERVE_ACTIVE_STATUS:
        raise ScienceError(423, "science compute reserve is locked until L2 marketplace activation")
    if bool(governance.get("emergency_paused", 0)):
        raise ScienceError(423, "science compute reserve is emergency paused")
    if not bool(governance.get("payouts_enabled", 0)):
        raise ScienceError(423, "science compute reserve payouts are disabled")


def _validate_epoch_payout_limit(connection: Any, amount: float) -> None:
    reserve = _ensure_reserve_epoch(connection, current_epoch())
    if round(float(reserve["total_paid"]) + amount, 8) > SCIENCE_MAX_PAYOUT_PER_EPOCH:
        raise ScienceError(429, "science compute reserve max_payout_per_epoch exceeded")


def _require_authorized_signer(governance: dict[str, Any], signer: str) -> None:
    authorized = _authorized_signers(governance)
    if authorized and signer not in authorized:
        raise ScienceError(403, "signer is not authorized for science reserve governance")


def _governance_approvals(governance: dict[str, Any]) -> list[str]:
    raw = governance.get("approvals") or "[]"
    return _decode_string_list(raw)


def _authorized_signers(governance: dict[str, Any]) -> list[str]:
    raw = governance.get("authorized_signers") or "[]"
    parsed = _decode_string_list(raw)
    return parsed or list(SCIENCE_RESERVE_AUTHORIZED_SIGNERS)


def _decode_string_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed]


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _record_science_event(
    connection: Any,
    event_type: str,
    *,
    address: str | None = None,
    job_id: str | None = None,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO science_events (event_type, address, job_id, payload, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_type, address, job_id, json.dumps(payload, sort_keys=True), utc_now()),
    )


def _apply_ledger_entry(
    connection: Any,
    *,
    account_id: str,
    account_type: str,
    amount: float,
    entry_type: str,
    block_height: int | None = None,
    related_id: str | None = None,
    description: str | None = None,
) -> None:
    _ensure_balance_account(connection, account_id, account_type)
    current = connection.execute(
        "SELECT balance FROM balances WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    balance_after = round(float(current["balance"]) + float(amount), 8)
    if balance_after < 0:
        raise ScienceError(409, "science ledger operation would create a negative balance")
    timestamp = utc_now()
    connection.execute(
        "UPDATE balances SET balance = ?, updated_at = ? WHERE account_id = ?",
        (balance_after, timestamp, account_id),
    )
    connection.execute(
        """
        INSERT INTO ledger_entries (
            account_id, account_type, amount, balance_after, entry_type,
            block_height, related_id, description, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            account_type,
            round(float(amount), 8),
            balance_after,
            entry_type,
            block_height,
            related_id,
            description,
            timestamp,
        ),
    )


def _ensure_balance_account(connection: Any, account_id: str, account_type: str) -> None:
    connection.execute(
        """
        INSERT INTO balances (account_id, account_type, balance, updated_at)
        VALUES (?, ?, 0, ?)
        ON CONFLICT(account_id) DO NOTHING
        """,
        (account_id, account_type, utc_now()),
    )


def _science_event_message(
    event_type: str,
    address: str | None,
    job_id: str | None,
    payload: dict[str, Any],
) -> str:
    if event_type == "ScienceReserveAccrued":
        return f"reserve +{payload.get('amount')} en bloque {payload.get('block_height')}"
    if event_type == "ScienceJobPaid":
        return f"worker {address} cobro {payload.get('amount')} por {job_id}"
    if event_type in {"ScienceReservePaused", "ScienceReserveUnpaused", "ScienceReserveLocked", "ScienceReserveUnlocked"}:
        return f"reserve -> {payload.get('status', event_type)}"
    if job_id:
        return f"{job_id} -> {payload.get('status', event_type)}"
    return f"{address or 'science'} -> {event_type}"


def _clean_address(address: str) -> str:
    value = str(address or "").strip()
    if not value:
        raise ScienceError(400, "address is required")
    if len(value) > 128:
        raise ScienceError(400, "address is too long")
    return value


def _clean_text(value: str, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ScienceError(400, f"{field} is required")
    if len(text) > 512:
        raise ScienceError(400, f"{field} is too long")
    return text


def _clean_non_negative(value: float | None, field: str) -> float:
    amount = round(float(value or 0), 8)
    if amount < 0:
        raise ScienceError(400, f"{field} cannot be negative")
    return amount


def _clean_compute_units(value: float | None, field: str) -> float:
    units = _clean_non_negative(value, field)
    return units


def _clean_optional_hash(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    text = _clean_text(value, field)
    if len(text) > 256:
        raise ScienceError(400, f"{field} is too long")
    return text
