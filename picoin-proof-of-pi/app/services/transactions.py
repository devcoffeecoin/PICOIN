from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.crypto import canonical_json, sha256_text
from app.core.money import canonical_amount, to_units, units_from_db, units_to_float
from app.core.settings import (
    CHAIN_ID,
    FAUCET_ALLOWED_NETWORKS,
    FAUCET_MAX_AMOUNT,
    FAUCET_RATE_LIMIT_MAX_REQUESTS,
    FAUCET_RATE_LIMIT_WINDOW_SECONDS,
    GENESIS_ACCOUNT_ID,
    MAX_TRANSACTIONS_PER_BLOCK,
    MIN_TX_FEE_UNITS,
    NETWORK_ID,
    SCIENCE_MAX_PENDING_PER_REQUESTER,
)
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
from app.services.wallet import address_matches_public_key, is_valid_address, transaction_hash, unsigned_transaction_payload


SUPPORTED_BLOCK_TX_TYPES = {"transfer", "stake", "unstake", "science_job_create", "governance_action", "treasury_claim", "faucet"}
SCIENCE_RESERVE_GOVERNANCE_ACTIONS = {
    "propose_activation",
    "approve_activation",
    "execute_activation",
    "pause",
    "unpause",
}
EMPTY_TX_MERKLE_ROOT = sha256_text(canonical_json([]))


class TransactionExecutionError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def select_block_transactions(connection: Any, limit: int = MAX_TRANSACTIONS_PER_BLOCK) -> list[dict[str, Any]]:
    return select_transactions_for_task(connection, limit, _latest_height(connection))


def canonical_tx_hashes(tx_hashes: list[str] | tuple[str, ...] | None) -> list[str]:
    if not tx_hashes:
        return []
    return [str(tx_hash) for tx_hash in tx_hashes]


def canonical_empty_tx_merkle_root() -> str:
    return EMPTY_TX_MERKLE_ROOT


def selected_tx_hashes_hash(tx_hashes: list[str] | tuple[str, ...] | None) -> str:
    return sha256_text(canonical_json(canonical_tx_hashes(tx_hashes)))


def canonical_selected_tx_hashes_hash(tx_hashes: list[str] | tuple[str, ...] | None) -> str:
    return selected_tx_hashes_hash(tx_hashes)


def select_transactions_for_task(connection: Any, max_count: int, chain_height: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM mempool_transactions
        WHERE status = 'pending'
        ORDER BY fee_units DESC, created_at ASC, tx_hash ASC
        """,
    ).fetchall()
    max_count = max(0, int(max_count))
    if max_count == 0:
        return []

    expected_nonce_by_sender: dict[str, int] = {}
    reserved_units_by_sender: dict[str, int] = {}
    selected: list[dict[str, Any]] = []
    selected_hashes: list[str] = []
    progress = True
    while progress and len(selected) < max_count:
        progress = False
        for row in rows:
            if len(selected) >= max_count:
                break
            tx = decode_mempool_transaction(row_to_dict(row))
            if tx["tx_hash"] in selected_hashes:
                continue
            if tx["tx_type"] not in SUPPORTED_BLOCK_TX_TYPES:
                _fail_transaction(connection, tx["tx_hash"], "unsupported transaction type for block execution")
                continue
            reason = _basic_transaction_rejection_reason(tx)
            if reason:
                _fail_transaction(connection, tx["tx_hash"], reason)
                continue
            sender = tx["sender"]
            expected_nonce = expected_nonce_by_sender.get(sender)
            if expected_nonce is None:
                expected_nonce = _confirmed_nonce(connection, sender) + 1
            tx_nonce = int(tx["nonce"])
            if tx_nonce < expected_nonce:
                _fail_transaction(connection, tx["tx_hash"], f"invalid nonce, expected {expected_nonce}")
                continue
            if tx_nonce > expected_nonce:
                # Nonce gap: keep the transaction pending for a future pass/block.
                continue
            semantic_reason = _transaction_rejection_reason(connection, tx, expected_nonce_by_sender)
            if semantic_reason:
                _fail_transaction(connection, tx["tx_hash"], semantic_reason)
                continue
            debit_units = _tx_amount_units(tx) + _tx_fee_units(tx)
            if _balance_units(connection, sender) < reserved_units_by_sender.get(sender, 0) + debit_units:
                continue
            selected.append(tx)
            selected_hashes.append(tx["tx_hash"])
            expected_nonce_by_sender[sender] = tx_nonce + 1
            reserved_units_by_sender[sender] = reserved_units_by_sender.get(sender, 0) + debit_units
            progress = True
    return selected


def transaction_commitment(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    tx_hashes = canonical_tx_hashes([tx["tx_hash"] for tx in transactions])
    fee_units = sum(_tx_fee_units(tx) for tx in transactions)
    return {
        "tx_count": len(tx_hashes),
        "tx_hashes": tx_hashes,
        "tx_merkle_root": merkle_root(tx_hashes),
        "selected_tx_hashes_hash": selected_tx_hashes_hash(tx_hashes),
        "tx_fee_total_units": fee_units,
        "fee_reward": units_to_float(fee_units),
    }


def canonical_tx_commitment(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    return transaction_commitment(transactions)


def freeze_transactions_for_task(
    connection: Any,
    *,
    task_id: str,
    block_height: int,
    max_count: int = MAX_TRANSACTIONS_PER_BLOCK,
    timestamp: str | None = None,
) -> dict[str, Any]:
    timestamp = timestamp or utc_now()
    transactions = select_transactions_for_task(connection, max_count, block_height - 1)
    commitment = transaction_commitment(transactions)
    snapshot_id = sha256_text(
        canonical_json(
            {
                "block_height": int(block_height),
                "task_id": task_id,
                "tx_hashes": commitment["tx_hashes"],
                "tx_merkle_root": commitment["tx_merkle_root"],
                "tx_fee_total_units": commitment["tx_fee_total_units"],
            }
        )
    )
    tx_hashes_json = json.dumps(commitment["tx_hashes"], sort_keys=True, separators=(",", ":"))
    connection.execute(
        """
        INSERT INTO task_tx_snapshots (
            snapshot_id, task_id, block_height, tx_hashes_json, tx_merkle_root,
            tx_count, tx_fee_total_units, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            snapshot_id = excluded.snapshot_id,
            block_height = excluded.block_height,
            tx_hashes_json = excluded.tx_hashes_json,
            tx_merkle_root = excluded.tx_merkle_root,
            tx_count = excluded.tx_count,
            tx_fee_total_units = excluded.tx_fee_total_units,
            created_at = excluded.created_at
        """,
        (
            snapshot_id,
            task_id,
            int(block_height),
            tx_hashes_json,
            commitment["tx_merkle_root"],
            commitment["tx_count"],
            commitment["tx_fee_total_units"],
            timestamp,
        ),
    )
    for tx_hash in commitment["tx_hashes"]:
        connection.execute(
            """
            UPDATE mempool_transactions
            SET status = 'selected',
                selected_task_id = ?,
                selected_block_height = ?,
                mempool_snapshot_id = ?,
                selected_at = ?,
                released_at = NULL,
                failure_reason = NULL,
                updated_at = ?
            WHERE tx_hash = ? AND status = 'pending'
            """,
            (task_id, int(block_height), snapshot_id, timestamp, timestamp, tx_hash),
        )
    connection.execute(
        """
        UPDATE tasks
        SET mempool_snapshot_id = ?,
            selected_tx_hashes = ?,
            tx_merkle_root = ?,
            tx_count = ?,
            tx_fee_total_units = ?,
            selected_tx_hashes_hash = ?
        WHERE task_id = ?
        """,
        (
            snapshot_id,
            tx_hashes_json,
            commitment["tx_merkle_root"],
            commitment["tx_count"],
            commitment["tx_fee_total_units"],
            commitment["selected_tx_hashes_hash"],
            task_id,
        ),
    )
    return {
        "snapshot_id": snapshot_id,
        "task_id": task_id,
        "block_height": int(block_height),
        **commitment,
    }


def get_task_tx_snapshot(connection: Any, task_id: str) -> dict[str, Any] | None:
    row = row_to_dict(connection.execute("SELECT * FROM task_tx_snapshots WHERE task_id = ?", (task_id,)).fetchone())
    if row is None:
        return None
    tx_hashes = _decode_json(row.get("tx_hashes_json"), [])
    return {
        **row,
        "selected_tx_hashes": tx_hashes,
        "selected_tx_hashes_hash": selected_tx_hashes_hash(tx_hashes),
        "tx_hashes": tx_hashes,
        "tx_count": int(row.get("tx_count") or len(tx_hashes)),
        "tx_fee_total_units": int(row.get("tx_fee_total_units") or 0),
    }


def load_snapshot_transactions(connection: Any, task_id: str) -> list[dict[str, Any]]:
    snapshot = get_task_tx_snapshot(connection, task_id)
    if snapshot is None:
        return []
    transactions: list[dict[str, Any]] = []
    for tx_hash in snapshot["selected_tx_hashes"]:
        row = row_to_dict(connection.execute("SELECT * FROM mempool_transactions WHERE tx_hash = ?", (tx_hash,)).fetchone())
        if row is None:
            raise TransactionExecutionError(f"snapshot transaction missing: {tx_hash}")
        transactions.append(decode_mempool_transaction(row))
    return transactions


def release_selected_transactions(connection: Any, task_id: str, reason: str, timestamp: str | None = None) -> dict[str, Any]:
    timestamp = timestamp or utc_now()
    rows = connection.execute(
        """
        SELECT tx_hash, expires_at
        FROM mempool_transactions
        WHERE selected_task_id = ? AND status = 'selected'
        ORDER BY tx_hash ASC
        """,
        (task_id,),
    ).fetchall()
    released = 0
    expired = 0
    for row in rows:
        expires_at = str(row["expires_at"] or "")
        status = "expired" if expires_at and expires_at < timestamp else "pending"
        if status == "expired":
            expired += 1
        else:
            released += 1
        connection.execute(
            """
            UPDATE mempool_transactions
            SET status = ?,
                selected_task_id = NULL,
                selected_block_height = NULL,
                mempool_snapshot_id = NULL,
                released_at = ?,
                failure_reason = ?,
                rejection_reason = CASE WHEN ? = 'expired' THEN 'ttl expired' ELSE rejection_reason END,
                updated_at = ?
            WHERE tx_hash = ?
            """,
            (status, timestamp, reason, status, timestamp, row["tx_hash"]),
        )
    return {"released": released, "expired": expired, "reason": reason}


def apply_block_transactions(
    connection: Any,
    *,
    miner_id: str,
    miner_account_type: str = "miner",
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
        elif tx["tx_type"] == "faucet":
            _apply_faucet_transaction(connection, tx, block_height, timestamp)
        _apply_fee_reward(connection, miner_id, miner_account_type, tx, block_height, timestamp)
        connection.execute(
            """
            UPDATE mempool_transactions
            SET status = 'confirmed',
                block_height = ?,
                selected_task_id = NULL,
                selected_block_height = NULL,
                confirmed_at = ?,
                rejection_reason = NULL,
                failure_reason = NULL,
                updated_at = ?
            WHERE tx_hash = ?
            """,
            (block_height, timestamp, timestamp, tx["tx_hash"]),
        )
        _record_confirmed_nonce(connection, tx["sender"], int(tx["nonce"]), timestamp)
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
                tx_hash, tx_type, sender, recipient, amount, amount_units, nonce, fee, fee_units,
                payload, public_key, signature, status, propagated,
                block_height, rejection_reason, expires_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1, NULL, NULL, ?, ?, ?)
            """,
            (
                tx["tx_hash"],
                tx["tx_type"],
                tx["sender"],
                tx.get("recipient"),
                units_to_float(_tx_amount_units(tx)),
                _tx_amount_units(tx),
                int(tx["nonce"]),
                units_to_float(_tx_fee_units(tx)),
                _tx_fee_units(tx),
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
        "amount": units_to_float(_tx_amount_units({**row, **unsigned_payload})),
        "amount_units": _tx_amount_units({**row, **unsigned_payload}),
        "fee": units_to_float(_tx_fee_units({**row, **unsigned_payload})),
        "fee_units": _tx_fee_units({**row, **unsigned_payload}),
        "nonce": int(unsigned_payload.get("nonce", row.get("nonce", 0))),
        "payload": unsigned_payload.get("payload", {}),
        "propagated": bool(row.get("propagated")),
    }


def get_wallet_nonce_status(connection: Any, address: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT
            COALESCE(MAX(CASE WHEN status IN ('pending', 'propagated', 'selected', 'released') THEN nonce ELSE 0 END), 0) AS pending_nonce,
            COALESCE(SUM(CASE WHEN status IN ('pending', 'propagated', 'selected', 'released') THEN 1 ELSE 0 END), 0) AS pending_count
        FROM mempool_transactions
        WHERE sender = ?
        """,
        (address,),
    ).fetchone()
    confirmed_nonce = _confirmed_nonce(connection, address)
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
    level = canonical_tx_hashes(tx_hashes)
    if not level:
        return canonical_empty_tx_merkle_root()
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [
            sha256_text(canonical_json({"left": level[index], "right": level[index + 1]}))
            for index in range(0, len(level), 2)
        ]
    return level[0]


def _latest_height(connection: Any) -> int:
    row = connection.execute("SELECT COALESCE(MAX(height), 0) AS height FROM blocks").fetchone()
    return int(row["height"] if row else 0)


def _basic_transaction_rejection_reason(tx: dict[str, Any]) -> str | None:
    if tx["tx_type"] not in SUPPORTED_BLOCK_TX_TYPES:
        return "unsupported transaction type for block execution"
    if not _is_signature_valid(tx):
        return "invalid transaction signature"
    if tx.get("network_id") != NETWORK_ID or tx.get("chain_id") != CHAIN_ID:
        return "transaction network or chain mismatch"
    if _tx_fee_units(tx) < MIN_TX_FEE_UNITS:
        return "transaction fee below minimum"
    if _tx_amount_units(tx) < 0:
        return "amount must be non-negative"
    return None


def _balance_units(connection: Any, account_id: str) -> int:
    row = connection.execute("SELECT balance_units FROM balances WHERE account_id = ?", (account_id,)).fetchone()
    return int(row["balance_units"] if row else 0)


def _confirmed_nonce(connection: Any, account_id: str) -> int:
    nonce_row = connection.execute(
        "SELECT nonce FROM account_nonces WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    stored_nonce = int(nonce_row["nonce"]) if nonce_row is not None else 0
    confirmed_row = connection.execute(
        """
        SELECT COALESCE(MAX(nonce), 0) AS nonce
        FROM mempool_transactions
        WHERE sender = ? AND status = 'confirmed'
        """,
        (account_id,),
    ).fetchone()
    mempool_nonce = int(confirmed_row["nonce"] if confirmed_row else 0)
    return max(stored_nonce, mempool_nonce)


def _record_confirmed_nonce(connection: Any, account_id: str, nonce: int, timestamp: str) -> None:
    connection.execute(
        """
        INSERT INTO account_nonces (account_id, nonce, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            nonce = CASE WHEN excluded.nonce > account_nonces.nonce THEN excluded.nonce ELSE account_nonces.nonce END,
            updated_at = excluded.updated_at
        """,
        (account_id, int(nonce), timestamp),
    )


def _tx_amount_units(tx: dict[str, Any]) -> int:
    if tx.get("amount_units") is not None:
        return int(tx.get("amount_units") or 0)
    return to_units(tx.get("amount", 0))


def _tx_fee_units(tx: dict[str, Any]) -> int:
    if tx.get("fee_units") is not None:
        return int(tx.get("fee_units") or 0)
    return to_units(tx.get("fee", 0))


def _fail_transaction(connection: Any, tx_hash: str, reason: str) -> None:
    connection.execute(
        """
        UPDATE mempool_transactions
        SET status = 'failed',
            failure_reason = ?,
            rejection_reason = ?,
            updated_at = ?
        WHERE tx_hash = ?
        """,
        (reason, reason, utc_now(), tx_hash),
    )


def _transaction_rejection_reason(
    connection: Any,
    tx: dict[str, Any],
    expected_nonce_by_sender: dict[str, int],
) -> str | None:
    if tx["tx_type"] not in SUPPORTED_BLOCK_TX_TYPES:
        return "unsupported transaction type for block execution"
    if not _is_signature_valid(tx):
        return "invalid transaction signature"
    if _tx_fee_units(tx) < 0:
        return "fee must be non-negative"
    sender = tx["sender"]
    expected_nonce = expected_nonce_by_sender.get(sender)
    if expected_nonce is None:
        expected_nonce = _confirmed_nonce(connection, sender) + 1
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
    if tx["tx_type"] == "faucet":
        return _faucet_rejection_reason(connection, tx)
    return None


def _transfer_rejection_reason(tx: dict[str, Any]) -> str | None:
    if _tx_amount_units(tx) <= 0:
        return "transfer amount must be positive"
    if not is_valid_address(tx.get("recipient")):
        return "transfer transaction requires a valid PI recipient"
    return None


def _faucet_rejection_reason(connection: Any, tx: dict[str, Any]) -> str | None:
    if NETWORK_ID not in FAUCET_ALLOWED_NETWORKS:
        return f"faucet is disabled on network '{NETWORK_ID}'"
    amount = round(float(tx.get("amount", 0)), 8)
    if amount <= 0:
        return "faucet amount must be positive"
    if amount > FAUCET_MAX_AMOUNT:
        return f"faucet amount exceeds max {FAUCET_MAX_AMOUNT}"
    if tx.get("recipient") not in {None, "", tx["sender"]}:
        return "faucet transaction recipient must be empty or sender"
    if round(float(tx.get("fee", 0)), 8) != 0:
        return "faucet transaction fee must be zero"
    genesis_balance = _balance(connection, GENESIS_ACCOUNT_ID)
    if genesis_balance < amount:
        return "genesis faucet balance is insufficient"
    window_start = (datetime.fromisoformat(tx["timestamp"]) - timedelta(seconds=FAUCET_RATE_LIMIT_WINDOW_SECONDS)).isoformat()
    recent_requests = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM ledger_entries
        WHERE account_id = ?
          AND entry_type = 'faucet_credit'
          AND created_at >= ?
        """,
        (tx["sender"], window_start),
    ).fetchone()
    if int(recent_requests["count"]) >= FAUCET_RATE_LIMIT_MAX_REQUESTS:
        return "faucet rate limit exceeded for account"
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


def _apply_faucet_transaction(connection: Any, tx: dict[str, Any], block_height: int, timestamp: str) -> None:
    amount = round(float(tx.get("amount", 0)), 8)
    sender = tx["sender"]
    _apply_account_delta(
        connection,
        GENESIS_ACCOUNT_ID,
        "genesis",
        -amount,
        "faucet_debit",
        block_height,
        tx["tx_hash"],
        f"{NETWORK_ID} faucet debit for wallet",
        timestamp,
    )
    _apply_account_delta(
        connection,
        sender,
        "wallet",
        amount,
        "faucet_credit",
        block_height,
        tx["tx_hash"],
        f"{NETWORK_ID} faucet credit",
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


def _apply_fee_reward(
    connection: Any,
    miner_id: str,
    miner_account_type: str,
    tx: dict[str, Any],
    block_height: int,
    timestamp: str,
) -> None:
    fee = round(float(tx.get("fee", 0)), 8)
    if fee <= 0:
        return
    _apply_account_delta(
        connection,
        miner_id,
        miner_account_type,
        fee,
        "transaction_fee_reward",
        block_height,
        tx["tx_hash"],
        "transaction fee reward",
        timestamp,
    )


def _total_debit(tx: dict[str, Any]) -> float:
    if tx["tx_type"] in {"transfer", "stake"}:
        return units_to_float(_tx_amount_units(tx) + _tx_fee_units(tx))
    if tx["tx_type"] == "faucet":
        return 0.0
    return units_to_float(_tx_fee_units(tx))


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
    row = connection.execute("SELECT balance, balance_units FROM balances WHERE account_id = ?", (account_id,)).fetchone()
    return 0.0 if row is None else units_to_float(units_from_db(row["balance"], row["balance_units"]))


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
    current = connection.execute(
        "SELECT balance, balance_units FROM balances WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    amount_units = to_units(amount)
    previous_units = units_from_db(current["balance"], current["balance_units"]) if current is not None else 0
    balance_after_units = previous_units + amount_units
    balance_after = units_to_float(balance_after_units)
    if balance_after_units < 0:
        raise TransactionExecutionError(f"negative balance for {account_id}")
    connection.execute(
        """
        INSERT INTO balances (account_id, account_type, balance, balance_units, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            account_type = excluded.account_type,
            balance = excluded.balance,
            balance_units = excluded.balance_units,
            updated_at = excluded.updated_at
        """,
        (account_id, account_type, balance_after, balance_after_units, timestamp),
    )
    connection.execute(
        """
        INSERT INTO ledger_entries (
            account_id, account_type, amount, amount_units, balance_after, balance_after_units, entry_type,
            block_height, related_id, description, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            account_type,
            units_to_float(amount_units),
            amount_units,
            balance_after,
            balance_after_units,
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
            and address_matches_public_key(tx.get("sender"), tx.get("public_key"))
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
        amount=canonical_amount(_tx_amount_units(tx)),
        nonce=int(tx["nonce"]),
        fee=canonical_amount(_tx_fee_units(tx)),
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
