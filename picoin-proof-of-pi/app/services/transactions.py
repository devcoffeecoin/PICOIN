from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.crypto import canonical_json, sha256_text
from app.core.settings import CHAIN_ID, MAX_TRANSACTIONS_PER_BLOCK, NETWORK_ID
from app.core.signatures import verify_payload_signature
from app.db.database import row_to_dict
from app.services.wallet import address_from_public_key, transaction_hash, unsigned_transaction_payload


SUPPORTED_BLOCK_TX_TYPES = {"transfer"}


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
        ORDER BY created_at ASC, tx_hash ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    selected: list[dict[str, Any]] = []
    for row in rows:
        tx = decode_mempool_transaction(row_to_dict(row))
        if tx["tx_type"] not in SUPPORTED_BLOCK_TX_TYPES:
            continue
        if not _is_signature_valid(tx):
            _reject_transaction(connection, tx["tx_hash"], "invalid transaction signature")
            continue
        selected.append(tx)
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
            _reject_transaction(connection, tx["tx_hash"], reason)
            rejected.append({"tx_hash": tx["tx_hash"], "reason": reason})
            continue

        amount = round(float(tx.get("amount", 0)), 8)
        fee = round(float(tx.get("fee", 0)), 8)
        total_debit = round(amount + fee, 8)
        sender = tx["sender"]
        recipient = tx.get("recipient")
        if not recipient:
            reason = "transfer transaction requires recipient"
            _reject_transaction(connection, tx["tx_hash"], reason)
            rejected.append({"tx_hash": tx["tx_hash"], "reason": reason})
            continue

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
        if fee > 0:
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
        expected_nonce_by_sender[sender] = int(tx["nonce"]) + 1
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
    if tx["tx_type"] != "transfer":
        return "unsupported transaction type for block execution"
    if not _is_signature_valid(tx):
        return "invalid transaction signature"
    if float(tx.get("amount", 0)) <= 0:
        return "transfer amount must be positive"
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
    total_debit = round(float(tx.get("amount", 0)) + float(tx.get("fee", 0)), 8)
    if balance < total_debit:
        return "insufficient balance"
    return None


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
