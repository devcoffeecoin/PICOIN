from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.crypto import canonical_json, sha256_text
from app.core.settings import CHAIN_ID, MAX_TRANSACTIONS_PER_BLOCK, NETWORK_ID, SCIENCE_MAX_PENDING_PER_REQUESTER
from app.core.signatures import verify_payload_signature
from app.db.database import row_to_dict
from app.services.science import (
    ScienceError,
    approve_science_reserve_activation_in_connection,
    create_science_job_in_connection,
    current_epoch,
    determine_science_tier,
    execute_science_reserve_activation_in_connection,
    pause_science_reserve_in_connection,
    propose_science_reserve_activation_in_connection,
    stake_science_access_in_connection,
    unstake_science_access_in_connection,
    unpause_science_reserve_in_connection,
    _monthly_quota_limit,
    _normalize_compute_budget,
)
from app.services import treasury as treasury_service
from app.services.treasury import TreasuryError, claim_scientific_development_treasury_in_connection
from app.services.wallet import address_from_public_key, is_valid_address, transaction_hash, unsigned_transaction_payload


SUPPORTED_BLOCK_TX_TYPES = {"transfer", "stake", "unstake", "science_job_create", "governance_action", "treasury_claim"}
SCIENCE_RESERVE_GOVERNANCE_ACTIONS = {
    "propose_activation",
    "approve_activation",
    "execute_activation",
    "pause",
    "unpause",
}


class TransactionExecutionError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def select_block_transactions(connection: Any, limit: int = MAX_TRANSACTIONS_PER_BLOCK) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM mempool_transactions
        WHERE status IN ('pending', 'propagated')
        ORDER BY sender ASC, nonce ASC, created_at ASC, tx_hash ASC
        """,
    ).fetchall()
    executable_by_sender: dict[str, list[dict[str, Any]]] = {}
    expected_nonce_by_sender: dict[str, int] = {}
    reserved_debit_by_sender: dict[str, float] = {}
    for row in rows:
        tx = decode_mempool_transaction(row_to_dict(row))
        if tx["tx_type"] not in SUPPORTED_BLOCK_TX_TYPES:
            continue
        reason = _transaction_rejection_reason(connection, tx, expected_nonce_by_sender)
        if reason:
            _reject_transaction(connection, tx["tx_hash"], reason)
            continue
        sender = tx["sender"]
        reserved_debit = reserved_debit_by_sender.get(sender, 0.0)
        total_debit = _total_debit(tx)
        if _balance(connection, sender) < round(reserved_debit + total_debit, 8):
            _reject_transaction(connection, tx["tx_hash"], "insufficient balance")
            continue
        executable_by_sender.setdefault(tx["sender"], []).append(tx)
        expected_nonce_by_sender[tx["sender"]] = int(tx["nonce"]) + 1
        reserved_debit_by_sender[sender] = round(reserved_debit + total_debit, 8)

    selected: list[dict[str, Any]] = []
    while len(selected) < limit:
        heads = [transactions[0] for transactions in executable_by_sender.values() if transactions]
        if not heads:
            break
        next_tx = min(heads, key=_transaction_priority)
        selected.append(next_tx)
        executable_by_sender[next_tx["sender"]].pop(0)
    return selected


def transaction_commitment(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    tx_hashes = [tx["tx_hash"] for tx in transactions]
    return {
        "tx_count": len(tx_hashes),
        "tx_hashes": tx_hashes,
        "tx_merkle_root": merkle_root(tx_hashes) if tx_hashes else None,
        "fee_reward": round(sum(float(tx.get("fee", 0)) for tx in transactions), 8),
    }


def apply_block_transactions(
    connection: Any,
    *,
    miner_id: str,
    block_height: int,
    transactions: list[dict[str, Any]],
    timestamp: str | None = None,
) -> dict[str, Any]:
    timestamp = timestamp or utc_now()
    applied: list[str] = []
    rejected: list[dict[str, str]] = []
    expected_nonce_by_sender: dict[str, int] = {}

    for tx in transactions:
        reason = _transaction_rejection_reason(connection, tx, expected_nonce_by_sender)
        if reason:
            raise TransactionExecutionError(f"block transaction {tx['tx_hash']} is invalid: {reason}")

        if tx["tx_type"] == "transfer":
            _apply_transfer_transaction(connection, tx, block_height, timestamp)
        elif tx["tx_type"] == "stake":
            _apply_science_stake_transaction(connection, tx, block_height, timestamp)
        elif tx["tx_type"] == "unstake":
            _apply_science_unstake_transaction(connection, tx, block_height, timestamp)
        elif tx["tx_type"] == "science_job_create":
            _apply_science_job_create_transaction(connection, tx, block_height, timestamp)
        elif tx["tx_type"] == "governance_action":
            _apply_governance_action_transaction(connection, tx, block_height, timestamp)
        elif tx["tx_type"] == "treasury_claim":
            _apply_treasury_claim_transaction(connection, tx, block_height, timestamp)
        _apply_fee_reward(connection, miner_id, tx, block_height, timestamp)
        connection.execute(
            """
            UPDATE mempool_transactions
            SET status = 'confirmed',
                block_height = ?,
                rejection_reason = NULL,
                updated_at = ?
            WHERE tx_hash = ?
            """,
            (block_height, timestamp, tx["tx_hash"]),
        )
        expected_nonce_by_sender[tx["sender"]] = int(tx["nonce"]) + 1
        applied.append(tx["tx_hash"])

    return {"applied": applied, "rejected": rejected}


def ensure_block_transactions_in_mempool(connection: Any, transactions: list[dict[str, Any]], timestamp: str | None = None) -> None:
    timestamp = timestamp or utc_now()
    for tx in transactions:
        if not _is_signature_valid(tx):
            raise TransactionExecutionError(f"invalid signature for block transaction {tx.get('tx_hash')}")
        existing = connection.execute(
            "SELECT tx_hash FROM mempool_transactions WHERE tx_hash = ?",
            (tx["tx_hash"],),
        ).fetchone()
        if existing is not None:
            continue
        unsigned_payload = _unsigned_from_tx(tx)
        connection.execute(
            """
            INSERT INTO mempool_transactions (
                tx_hash, tx_type, sender, recipient, amount, nonce, fee,
                payload, public_key, signature, status, propagated,
                block_height, rejection_reason, expires_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1, NULL, NULL, ?, ?, ?)
            """,
            (
                tx["tx_hash"],
                tx["tx_type"],
                tx["sender"],
                tx.get("recipient"),
                float(tx.get("amount", 0)),
                int(tx["nonce"]),
                float(tx.get("fee", 0)),
                json.dumps(unsigned_payload, sort_keys=True),
                tx["public_key"],
                tx["signature"],
                timestamp,
                timestamp,
                timestamp,
            ),
        )


def decode_mempool_transaction(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    unsigned_payload = _decode_json(row.get("payload"), {})
    return {
        **row,
        **unsigned_payload,
        "amount": round(float(unsigned_payload.get("amount", row.get("amount", 0))), 8),
        "fee": round(float(unsigned_payload.get("fee", row.get("fee", 0))), 8),
        "nonce": int(unsigned_payload.get("nonce", row.get("nonce", 0))),
        "payload": unsigned_payload.get("payload", {}),
        "propagated": bool(row.get("propagated")),
    }


def get_wallet_nonce_status(connection: Any, address: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT
            COALESCE(MAX(CASE WHEN status = 'confirmed' THEN nonce ELSE 0 END), 0) AS confirmed_nonce,
            COALESCE(MAX(CASE WHEN status IN ('pending', 'propagated') THEN nonce ELSE 0 END), 0) AS pending_nonce,
            COALESCE(SUM(CASE WHEN status IN ('pending', 'propagated') THEN 1 ELSE 0 END), 0) AS pending_count
        FROM mempool_transactions
        WHERE sender = ?
        """,
        (address,),
    ).fetchone()
    confirmed_nonce = int(row["confirmed_nonce"] if row else 0)
    pending_nonce = int(row["pending_nonce"] if row else 0)
    return {
        "address": address,
        "confirmed_nonce": confirmed_nonce,
        "pending_nonce": pending_nonce,
        "next_nonce": max(confirmed_nonce, pending_nonce) + 1,
        "pending_count": int(row["pending_count"] if row else 0),
        "checked_at": utc_now(),
    }


def merkle_root(tx_hashes: list[str]) -> str:
    if not tx_hashes:
        return ""
    level = list(tx_hashes)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [
            sha256_text(canonical_json({"left": level[index], "right": level[index + 1]}))
            for index in range(0, len(level), 2)
        ]
    return level[0]


def _transaction_rejection_reason(
    connection: Any,
    tx: dict[str, Any],
    expected_nonce_by_sender: dict[str, int],
) -> str | None:
    if tx["tx_type"] not in SUPPORTED_BLOCK_TX_TYPES:
        return "unsupported transaction type for block execution"
    if not _is_signature_valid(tx):
        return "invalid transaction signature"
    if float(tx.get("fee", 0)) < 0:
        return "fee must be non-negative"
    sender = tx["sender"]
    expected_nonce = expected_nonce_by_sender.get(sender)
    if expected_nonce is None:
        confirmed = connection.execute(
            """
            SELECT COALESCE(MAX(nonce), 0) AS nonce
            FROM mempool_transactions
            WHERE sender = ? AND status = 'confirmed'
            """,
            (sender,),
        ).fetchone()
        expected_nonce = int(confirmed["nonce"]) + 1
    if int(tx["nonce"]) != expected_nonce:
        return f"invalid nonce, expected {expected_nonce}"
    balance = _balance(connection, sender)
    total_debit = _total_debit(tx)
    if balance < total_debit:
        return "insufficient balance"
    if tx["tx_type"] == "transfer":
        return _transfer_rejection_reason(tx)
    if tx["tx_type"] == "stake":
        return _stake_rejection_reason(connection, tx)
    if tx["tx_type"] == "unstake":
        return _unstake_rejection_reason(connection, tx)
    if tx["tx_type"] == "science_job_create":
        return _science_job_create_rejection_reason(connection, tx)
    if tx["tx_type"] == "governance_action":
        return _governance_action_rejection_reason(connection, tx)
    if tx["tx_type"] == "treasury_claim":
        return _treasury_claim_rejection_reason(connection, tx)
    return None


def _transfer_rejection_reason(tx: dict[str, Any]) -> str | None:
    if float(tx.get("amount", 0)) <= 0:
        return "transfer amount must be positive"
    if not is_valid_address(tx.get("recipient")):
        return "transfer transaction requires a valid PI recipient"
    return None


def _transaction_priority(tx: dict[str, Any]) -> tuple[float, str, str]:
    return (-float(tx.get("fee", 0)), str(tx.get("created_at") or ""), str(tx.get("tx_hash") or ""))


def _stake_rejection_reason(connection: Any, tx: dict[str, Any]) -> str | None:
    amount = round(float(tx.get("amount", 0)), 8)
    if amount <= 0:
        return "stake amount must be positive"
    existing = connection.execute(
        "SELECT stake_amount FROM science_stake_accounts WHERE address = ?",
        (tx["sender"],),
    ).fetchone()
    existing_amount = 0.0 if existing is None else float(existing["stake_amount"])
    if determine_science_tier(existing_amount + amount) is None:
        return "science stake is below researcher minimum"
    return None


def _unstake_rejection_reason(connection: Any, tx: dict[str, Any]) -> str | None:
    requested_amount = round(float(tx.get("amount", 0)), 8)
    account = row_to_dict(
        connection.execute(
            "SELECT * FROM science_stake_accounts WHERE address = ?",
            (tx["sender"],),
        ).fetchone()
    )
    if account is None or float(account.get("stake_amount") or 0) <= 0:
        return "active science stake is required"
    locked_amount = round(float(account["stake_amount"]), 8)
    if requested_amount not in {0.0, locked_amount}:
        return "partial science unstake is not supported"
    stake_balance = _balance(connection, f"science_stake:{tx['sender']}")
    if stake_balance < locked_amount:
        return "science stake ledger balance is insufficient"
    active_jobs = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM science_jobs
        WHERE requester_address = ?
        AND status IN ('created', 'queued', 'assigned', 'committed', 'submitted', 'verified', 'accepted')
        AND paid = 0
        """,
        (tx["sender"],),
    ).fetchone()["count"]
    if int(active_jobs) > 0:
        return "cannot unstake while science jobs are active"
    return None


def _science_job_create_rejection_reason(connection: Any, tx: dict[str, Any]) -> str | None:
    payload = tx.get("payload") or {}
    required = {"job_type", "metadata_hash", "storage_pointer"}
    missing = sorted(required - set(payload))
    if missing:
        return f"missing science job fields: {', '.join(missing)}"
    account = row_to_dict(
        connection.execute(
            "SELECT * FROM science_stake_accounts WHERE address = ?",
            (tx["sender"],),
        ).fetchone()
    )
    if account is None or account.get("status") != "active" or not account.get("tier"):
        return "active science stake is required"
    try:
        _, _, reward_cap = _science_job_budget_from_payload(payload)
    except ScienceError as exc:
        return exc.detail
    epoch = current_epoch()
    quota_used = 0.0 if account["monthly_quota_epoch"] != epoch else float(account["monthly_quota_used"])
    if quota_used + 1 > _monthly_quota_limit(account):
        return "monthly science quota exceeded"
    if reward_cap <= 0:
        return None
    pending_row = connection.execute(
        """
        SELECT COALESCE(SUM(max_reward), 0) AS pending
        FROM science_jobs
        WHERE requester_address = ?
        AND paid = 0
        AND status IN ('created', 'queued', 'assigned', 'committed', 'submitted', 'verified', 'accepted')
        """,
        (tx["sender"],),
    ).fetchone()
    if round(float(pending_row["pending"]) + reward_cap, 8) > SCIENCE_MAX_PENDING_PER_REQUESTER:
        return "requester science pending rewards exceed max_pending_per_requester"
    reserve = connection.execute(
        "SELECT * FROM science_reward_reserve WHERE epoch = ?",
        (epoch,),
    ).fetchone()
    available = 0.0
    if reserve is not None:
        available = round(
            float(reserve["total_reserved"]) - float(reserve["total_paid"]) - float(reserve["total_pending"]),
            8,
        )
    if reward_cap > available:
        return "science compute reserve is insufficient for reward budget"
    job_id = _science_job_id(tx)
    existing = connection.execute("SELECT 1 FROM science_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if existing is not None:
        return "science job already exists"
    return None


def _governance_action_rejection_reason(connection: Any, tx: dict[str, Any]) -> str | None:
    payload = tx.get("payload") or {}
    scope = str(payload.get("scope") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()
    if scope != "science_reserve":
        return "unsupported governance scope"
    if action not in SCIENCE_RESERVE_GOVERNANCE_ACTIONS:
        return "unsupported science reserve governance action"
    governance = _science_governance_row(connection)
    authorized = _authorized_science_governance_signers(governance)
    if authorized and tx["sender"] not in authorized:
        return "signer is not authorized for science reserve governance"
    approvals = _science_governance_approvals(governance)
    if action == "propose_activation":
        if governance["status"] == "L2_ACTIVE":
            return "science reserve is already active"
    elif action == "approve_activation":
        if governance["status"] == "L2_ACTIVE":
            return "science reserve is already active"
        if not governance["activation_requested_at"]:
            return "science reserve activation has not been proposed"
    elif action == "execute_activation":
        if governance["status"] == "L2_ACTIVE":
            return None
        if not governance["activation_available_at"]:
            return "science reserve activation has not been proposed"
        threshold = int(payload.get("threshold") or 2)
        if len(approvals) < threshold:
            return "science reserve activation lacks multisig approvals"
        available_at = datetime.fromisoformat(governance["activation_available_at"])
        tx_time = datetime.fromisoformat(tx["timestamp"])
        if tx_time < available_at:
            return f"science reserve activation timelock active until {governance['activation_available_at']}"
    return None


def _treasury_claim_rejection_reason(connection: Any, tx: dict[str, Any]) -> str | None:
    payload = tx.get("payload") or {}
    claim_to = str(payload.get("claim_to") or treasury_service.SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET).strip()
    if tx["sender"] != treasury_service.SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET:
        return "treasury claim requires configured governance wallet"
    if claim_to != treasury_service.SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET:
        return "treasury claim destination must match configured treasury wallet"
    treasury = _treasury_row(connection)
    unlocked = round(float(treasury["unlocked_balance"]), 8)
    matured = _matured_treasury_locked_amount(connection, tx["timestamp"])
    claimable = round(unlocked + matured, 8)
    if claimable <= 0:
        return f"treasury funds are locked until {treasury['next_unlock_at']}"
    current_balance = _balance(connection, treasury_service.SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID)
    if current_balance < claimable:
        return "treasury ledger balance is lower than unlocked treasury balance"
    claim_id = _treasury_claim_id(tx)
    existing = connection.execute(
        "SELECT 1 FROM scientific_development_treasury_claims WHERE claim_id = ?",
        (claim_id,),
    ).fetchone()
    if existing is not None:
        return "treasury claim already exists"
    return None


def _apply_transfer_transaction(connection: Any, tx: dict[str, Any], block_height: int, timestamp: str) -> None:
    amount = round(float(tx.get("amount", 0)), 8)
    fee = round(float(tx.get("fee", 0)), 8)
    total_debit = round(amount + fee, 8)
    sender = tx["sender"]
    recipient = tx.get("recipient")
    _apply_account_delta(
        connection,
        sender,
        "wallet",
        -total_debit,
        "transfer_debit",
        block_height,
        tx["tx_hash"],
        f"transfer debit to {recipient}",
        timestamp,
    )
    _apply_account_delta(
        connection,
        recipient,
        "wallet",
        amount,
        "transfer_credit",
        block_height,
        tx["tx_hash"],
        f"transfer credit from {sender}",
        timestamp,
    )


def _apply_science_stake_transaction(connection: Any, tx: dict[str, Any], block_height: int, timestamp: str) -> None:
    amount = round(float(tx.get("amount", 0)), 8)
    fee = round(float(tx.get("fee", 0)), 8)
    sender = tx["sender"]
    existing = connection.execute(
        "SELECT stake_amount FROM science_stake_accounts WHERE address = ?",
        (sender,),
    ).fetchone()
    existing_amount = 0.0 if existing is None else float(existing["stake_amount"])
    new_stake = round(existing_amount + amount, 8)
    _apply_account_delta(
        connection,
        sender,
        "wallet",
        -round(amount + fee, 8),
        "science_stake_debit",
        block_height,
        tx["tx_hash"],
        "science stake lock",
        timestamp,
    )
    _apply_account_delta(
        connection,
        f"science_stake:{sender}",
        "science_stake",
        amount,
        "science_stake_lock",
        block_height,
        tx["tx_hash"],
        "science stake lock",
        timestamp,
    )
    stake_science_access_in_connection(
        connection,
        sender,
        new_stake,
        account_id=f"science_{sha256_text(sender)[:16]}",
        timestamp=timestamp,
    )


def _apply_science_unstake_transaction(connection: Any, tx: dict[str, Any], block_height: int, timestamp: str) -> None:
    fee = round(float(tx.get("fee", 0)), 8)
    sender = tx["sender"]
    account = row_to_dict(
        connection.execute(
            "SELECT * FROM science_stake_accounts WHERE address = ?",
            (sender,),
        ).fetchone()
    )
    amount = round(float(account["stake_amount"]), 8)
    if fee > 0:
        _apply_account_delta(
            connection,
            sender,
            "wallet",
            -fee,
            "science_unstake_fee_debit",
            block_height,
            tx["tx_hash"],
            "science unstake fee",
            timestamp,
        )
    _apply_account_delta(
        connection,
        f"science_stake:{sender}",
        "science_stake",
        -amount,
        "science_stake_unlock",
        block_height,
        tx["tx_hash"],
        "science stake unlock",
        timestamp,
    )
    _apply_account_delta(
        connection,
        sender,
        "wallet",
        amount,
        "science_unstake_credit",
        block_height,
        tx["tx_hash"],
        "science stake unlock",
        timestamp,
    )
    unstake_science_access_in_connection(connection, sender, timestamp=timestamp)


def _apply_science_job_create_transaction(connection: Any, tx: dict[str, Any], block_height: int, timestamp: str) -> None:
    fee = round(float(tx.get("fee", 0)), 8)
    if fee > 0:
        _apply_account_delta(
            connection,
            tx["sender"],
            "wallet",
            -fee,
            "science_job_fee_debit",
            block_height,
            tx["tx_hash"],
            "science job create fee",
            timestamp,
        )
    payload = tx.get("payload") or {}
    try:
        create_science_job_in_connection(
            connection,
            tx["sender"],
            payload["job_type"],
            payload["metadata_hash"],
            payload["storage_pointer"],
            reward_budget=payload.get("reward_budget"),
            max_compute_units=payload.get("max_compute_units"),
            reward_per_compute_unit=payload.get("reward_per_compute_unit"),
            max_reward=payload.get("max_reward"),
            job_id=_science_job_id(tx),
            timestamp=timestamp,
        )
    except ScienceError as exc:
        raise TransactionExecutionError(exc.detail) from exc


def _apply_governance_action_transaction(connection: Any, tx: dict[str, Any], block_height: int, timestamp: str) -> None:
    fee = round(float(tx.get("fee", 0)), 8)
    if fee > 0:
        _apply_account_delta(
            connection,
            tx["sender"],
            "wallet",
            -fee,
            "governance_action_fee_debit",
            block_height,
            tx["tx_hash"],
            "governance action fee",
            timestamp,
        )
    payload = tx.get("payload") or {}
    action = str(payload.get("action") or "").strip().lower()
    try:
        if action == "propose_activation":
            propose_science_reserve_activation_in_connection(connection, tx["sender"], timestamp=timestamp)
        elif action == "approve_activation":
            approve_science_reserve_activation_in_connection(connection, tx["sender"], timestamp=timestamp)
        elif action == "execute_activation":
            execute_science_reserve_activation_in_connection(connection, timestamp=timestamp)
        elif action == "pause":
            pause_science_reserve_in_connection(connection, tx["sender"], timestamp=timestamp)
        elif action == "unpause":
            unpause_science_reserve_in_connection(connection, tx["sender"], timestamp=timestamp)
        else:
            raise TransactionExecutionError("unsupported science reserve governance action")
    except ScienceError as exc:
        raise TransactionExecutionError(exc.detail) from exc


def _apply_treasury_claim_transaction(connection: Any, tx: dict[str, Any], block_height: int, timestamp: str) -> None:
    fee = round(float(tx.get("fee", 0)), 8)
    if fee > 0:
        _apply_account_delta(
            connection,
            tx["sender"],
            "wallet",
            -fee,
            "treasury_claim_fee_debit",
            block_height,
            tx["tx_hash"],
            "scientific development treasury claim fee",
            timestamp,
        )
    payload = tx.get("payload") or {}
    try:
        claim_scientific_development_treasury_in_connection(
            connection,
            requested_by=tx["sender"],
            claim_to=payload.get("claim_to"),
            claim_id=_treasury_claim_id(tx),
            timestamp=timestamp,
        )
    except TreasuryError as exc:
        raise TransactionExecutionError(exc.detail) from exc


def _apply_fee_reward(connection: Any, miner_id: str, tx: dict[str, Any], block_height: int, timestamp: str) -> None:
    fee = round(float(tx.get("fee", 0)), 8)
    if fee <= 0:
        return
    _apply_account_delta(
        connection,
        miner_id,
        "miner",
        fee,
        "transaction_fee_reward",
        block_height,
        tx["tx_hash"],
        "transaction fee reward",
        timestamp,
    )


def _total_debit(tx: dict[str, Any]) -> float:
    amount = round(float(tx.get("amount", 0)), 8)
    fee = round(float(tx.get("fee", 0)), 8)
    if tx["tx_type"] in {"transfer", "stake"}:
        return round(amount + fee, 8)
    return fee


def _science_job_budget_from_payload(payload: dict[str, Any]) -> tuple[float, float, float]:
    return _normalize_compute_budget(
        reward_budget=payload.get("reward_budget"),
        max_compute_units=payload.get("max_compute_units"),
        reward_per_compute_unit=payload.get("reward_per_compute_unit"),
        max_reward=payload.get("max_reward"),
    )


def _science_job_id(tx: dict[str, Any]) -> str:
    payload = tx.get("payload") or {}
    explicit = str(payload.get("job_id") or "").strip()
    if explicit:
        return explicit
    return f"science_job_{tx['tx_hash'][:16]}"


def _treasury_claim_id(tx: dict[str, Any]) -> str:
    payload = tx.get("payload") or {}
    explicit = str(payload.get("claim_id") or "").strip()
    if explicit:
        return explicit
    return f"treasury_claim_{tx['tx_hash'][:16]}"


def _treasury_row(connection: Any) -> dict[str, Any]:
    row = row_to_dict(
        connection.execute(
            "SELECT * FROM scientific_development_treasury WHERE treasury_id = ?",
            (treasury_service.SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,),
        ).fetchone()
    )
    if row is not None:
        return row
    now = utc_now()
    connection.execute(
        """
        INSERT INTO scientific_development_treasury (
            treasury_id, total_accumulated, total_claimed, locked_balance,
            unlocked_balance, current_epoch, epoch_start_block, epoch_end_block,
            next_unlock_at, last_claim_at, treasury_wallet, governance_wallet,
            created_at, updated_at
        )
        VALUES (?, 0, 0, 0, 0, 'bootstrap', 0, 0, ?, NULL, ?, ?, ?, ?)
        """,
        (
            treasury_service.SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
            now,
            treasury_service.SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET,
            treasury_service.SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET,
            now,
            now,
        ),
    )
    return row_to_dict(
        connection.execute(
            "SELECT * FROM scientific_development_treasury WHERE treasury_id = ?",
            (treasury_service.SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,),
        ).fetchone()
    )


def _matured_treasury_locked_amount(connection: Any, timestamp: str) -> float:
    tx_time = datetime.fromisoformat(timestamp)
    rows = connection.execute(
        """
        SELECT locked_amount, unlock_at
        FROM scientific_development_treasury_epochs
        WHERE status = 'locked'
        AND locked_amount > 0
        """
    ).fetchall()
    total = 0.0
    for row in rows:
        unlock_at = datetime.fromisoformat(row["unlock_at"])
        if tx_time >= unlock_at:
            total = round(total + float(row["locked_amount"]), 8)
    return total


def _science_governance_row(connection: Any) -> dict[str, Any]:
    row = row_to_dict(connection.execute("SELECT * FROM science_reserve_governance WHERE id = 1").fetchone())
    if row is None:
        now = utc_now()
        connection.execute(
            """
            INSERT INTO science_reserve_governance (
                id, status, activation_requested_at, activation_available_at,
                activated_at, approvals, authorized_signers, payouts_enabled,
                emergency_paused, updated_at
            )
            VALUES (1, 'RESERVE_LOCKED', NULL, NULL, NULL, '[]', '[]', 0, 0, ?)
            """,
            (now,),
        )
        row = row_to_dict(connection.execute("SELECT * FROM science_reserve_governance WHERE id = 1").fetchone())
    return row


def _science_governance_approvals(governance: dict[str, Any]) -> list[str]:
    return _decode_string_list(governance.get("approvals"))


def _authorized_science_governance_signers(governance: dict[str, Any]) -> list[str]:
    return _decode_string_list(governance.get("authorized_signers"))


def _decode_string_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed]


def _reject_transaction(connection: Any, tx_hash: str, reason: str) -> None:
    connection.execute(
        """
        UPDATE mempool_transactions
        SET status = 'rejected',
            rejection_reason = ?,
            updated_at = ?
        WHERE tx_hash = ? AND status IN ('pending', 'propagated')
        """,
        (reason, utc_now(), tx_hash),
    )


def _balance(connection: Any, account_id: str) -> float:
    row = connection.execute("SELECT balance FROM balances WHERE account_id = ?", (account_id,)).fetchone()
    return 0.0 if row is None else round(float(row["balance"]), 8)


def _apply_account_delta(
    connection: Any,
    account_id: str,
    account_type: str,
    amount: float,
    entry_type: str,
    block_height: int,
    related_id: str,
    description: str,
    timestamp: str,
) -> None:
    previous = _balance(connection, account_id)
    balance_after = round(previous + float(amount), 8)
    if balance_after < -0.00000001:
        raise TransactionExecutionError(f"negative balance for {account_id}")
    connection.execute(
        """
        INSERT INTO balances (account_id, account_type, balance, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            account_type = excluded.account_type,
            balance = excluded.balance,
            updated_at = excluded.updated_at
        """,
        (account_id, account_type, balance_after, timestamp),
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


def _is_signature_valid(tx: dict[str, Any]) -> bool:
    try:
        unsigned_payload = _unsigned_from_tx(tx)
        return (
            tx.get("network_id") == NETWORK_ID
            and tx.get("chain_id") == CHAIN_ID
            and address_from_public_key(tx["public_key"]) == tx["sender"]
            and transaction_hash(unsigned_payload, tx["public_key"]) == tx["tx_hash"]
            and verify_payload_signature(tx["public_key"], unsigned_payload, tx["signature"])
        )
    except (KeyError, TypeError, ValueError, RuntimeError):
        return False


def _unsigned_from_tx(tx: dict[str, Any]) -> dict[str, Any]:
    return unsigned_transaction_payload(
        tx_type=tx["tx_type"],
        sender=tx["sender"],
        recipient=tx.get("recipient"),
        amount=float(tx.get("amount", 0)),
        nonce=int(tx["nonce"]),
        fee=float(tx.get("fee", 0)),
        payload=tx.get("payload") or {},
        timestamp=tx["timestamp"],
        network_id=tx.get("network_id", NETWORK_ID),
        chain_id=tx.get("chain_id", CHAIN_ID),
    )


def _decode_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
