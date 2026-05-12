import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.settings import (
    SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET,
    SCIENTIFIC_DEVELOPMENT_REWARD_PERCENT_OF_BLOCK,
    SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
    SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET,
    SCIENTIFIC_DEVELOPMENT_UNLOCK_INTERVAL_DAYS,
)
from app.db.database import get_connection, row_to_dict


TREASURY_EVENT_ACCRUAL = "scientific_development_treasury_accrual"
TREASURY_EVENT_CLAIM = "scientific_development_treasury_claim"


class TreasuryError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def utc_now() -> str:
    return utc_now_dt().isoformat()


def scientific_development_amount(block_reward: float) -> float:
    return round(float(block_reward) * SCIENTIFIC_DEVELOPMENT_REWARD_PERCENT_OF_BLOCK, 8)


def get_scientific_development_treasury() -> dict[str, Any]:
    with get_connection() as connection:
        _unlock_matured_epochs(connection)
        treasury = _treasury_row(connection)
        history = _treasury_history(connection)
    return _decode_treasury(treasury, history)


def claim_scientific_development_treasury(
    requested_by: str | None = None,
    claim_to: str | None = None,
) -> dict[str, Any]:
    requested_by = _clean_address(requested_by or SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET)
    claim_to = _clean_address(claim_to or SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET)
    if requested_by != SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET:
        raise TreasuryError(403, "treasury claim requires configured governance wallet")
    if claim_to != SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET:
        raise TreasuryError(403, "treasury claim destination must match configured treasury wallet")

    with get_connection() as connection:
        _unlock_matured_epochs(connection)
        treasury = _treasury_row(connection)
        amount = round(float(treasury["unlocked_balance"]), 8)
        if amount <= 0:
            raise TreasuryError(423, f"treasury funds are locked until {treasury['next_unlock_at']}")

        current_balance = _balance_amount(connection, SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID)
        if current_balance < amount:
            raise TreasuryError(500, "treasury ledger balance is lower than unlocked treasury balance")

        claim_id = f"treasury_claim_{uuid.uuid4().hex[:16]}"
        now = utc_now()
        _apply_ledger_entry(
            connection,
            account_id=SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
            account_type="scientific_development_treasury",
            amount=-amount,
            entry_type=TREASURY_EVENT_CLAIM,
            related_id=claim_id,
            description="scientific development treasury claim debit",
        )
        _apply_ledger_entry(
            connection,
            account_id=claim_to,
            account_type="treasury_wallet",
            amount=amount,
            entry_type=TREASURY_EVENT_CLAIM,
            related_id=claim_id,
            description="scientific development treasury claim",
        )
        connection.execute(
            """
            INSERT INTO scientific_development_treasury_claims (
                claim_id, amount, claim_to, requested_by, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (claim_id, amount, claim_to, requested_by, now),
        )
        connection.execute(
            """
            UPDATE scientific_development_treasury_epochs
            SET claimed_amount = claimed_amount + unlocked_amount,
                unlocked_amount = 0,
                status = 'claimed',
                updated_at = ?
            WHERE unlocked_amount > 0
            """,
            (now,),
        )
        connection.execute(
            """
            UPDATE scientific_development_treasury
            SET total_claimed = total_claimed + ?,
                unlocked_balance = 0,
                last_claim_at = ?,
                updated_at = ?
            WHERE treasury_id = ?
            """,
            (amount, now, now, SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID),
        )
        treasury = _treasury_row(connection)
        history = _treasury_history(connection)
    decoded = _decode_treasury(treasury, history)
    decoded["claim"] = {
        "claim_id": claim_id,
        "amount": amount,
        "claim_to": claim_to,
        "requested_by": requested_by,
        "created_at": now,
    }
    return decoded


def record_scientific_development_treasury_for_block(
    connection: Any,
    block_height: int,
    block_reward: float,
) -> float:
    amount = scientific_development_amount(block_reward)
    if amount <= 0:
        return 0.0
    if amount < 0:
        raise TreasuryError(400, "treasury accrual cannot be negative")

    epoch = _current_quarter_epoch()
    now = utc_now_dt()
    unlock_at = now + timedelta(days=SCIENTIFIC_DEVELOPMENT_UNLOCK_INTERVAL_DAYS)
    _ensure_treasury(connection)
    epoch_row = _ensure_epoch(connection, epoch, block_height, unlock_at.isoformat())
    next_unlock_at = _earliest_locked_unlock_at(connection) or epoch_row["unlock_at"]
    timestamp = now.isoformat()

    connection.execute(
        """
        UPDATE scientific_development_treasury_epochs
        SET locked_amount = locked_amount + ?,
            end_block = MAX(end_block, ?),
            updated_at = ?
        WHERE epoch = ?
        """,
        (amount, block_height, timestamp, epoch),
    )
    connection.execute(
        """
        UPDATE scientific_development_treasury
        SET total_accumulated = total_accumulated + ?,
            locked_balance = locked_balance + ?,
            current_epoch = ?,
            epoch_start_block = ?,
            epoch_end_block = ?,
            next_unlock_at = ?,
            updated_at = ?
        WHERE treasury_id = ?
        """,
        (
            amount,
            amount,
            epoch,
            int(epoch_row["start_block"]),
            block_height,
            next_unlock_at,
            timestamp,
            SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
        ),
    )
    _apply_ledger_entry(
        connection,
        account_id=SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
        account_type="scientific_development_treasury",
        amount=amount,
        entry_type=TREASURY_EVENT_ACCRUAL,
        block_height=block_height,
        related_id=f"block:{block_height}",
        description="scientific development fund time-locked accrual",
    )
    return amount


def _unlock_matured_epochs(connection: Any) -> None:
    now_dt = utc_now_dt()
    now = now_dt.isoformat()
    rows = connection.execute(
        """
        SELECT *
        FROM scientific_development_treasury_epochs
        WHERE status = 'locked'
        AND locked_amount > 0
        """
    ).fetchall()
    total_unlocked = 0.0
    for row in rows:
        unlock_at = _parse_iso(row["unlock_at"])
        if now_dt < unlock_at:
            continue
        amount = round(float(row["locked_amount"]), 8)
        total_unlocked = round(total_unlocked + amount, 8)
        connection.execute(
            """
            UPDATE scientific_development_treasury_epochs
            SET locked_amount = 0,
                unlocked_amount = unlocked_amount + ?,
                status = 'unlocked',
                updated_at = ?
            WHERE epoch = ?
            """,
            (amount, now, row["epoch"]),
        )

    if total_unlocked > 0:
        connection.execute(
            """
            UPDATE scientific_development_treasury
            SET locked_balance = MAX(0, locked_balance - ?),
                unlocked_balance = unlocked_balance + ?,
                next_unlock_at = COALESCE(?, next_unlock_at),
                updated_at = ?
            WHERE treasury_id = ?
            """,
            (
                total_unlocked,
                total_unlocked,
                _earliest_locked_unlock_at(connection),
                now,
                SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
            ),
        )


def _decode_treasury(treasury: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    unlocked = round(float(treasury["unlocked_balance"]), 8)
    locked = round(float(treasury["locked_balance"]), 8)
    return {
        **treasury,
        "total_accumulated": round(float(treasury["total_accumulated"]), 8),
        "total_claimed": round(float(treasury["total_claimed"]), 8),
        "locked_balance": locked,
        "unlocked_balance": unlocked,
        "claimable": unlocked,
        "unlock_interval_days": SCIENTIFIC_DEVELOPMENT_UNLOCK_INTERVAL_DAYS,
        "reward_percent": SCIENTIFIC_DEVELOPMENT_REWARD_PERCENT_OF_BLOCK,
        "history": history,
    }


def _treasury_history(connection: Any, limit: int = 12) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM scientific_development_treasury_epochs
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def _treasury_row(connection: Any) -> dict[str, Any]:
    _ensure_treasury(connection)
    return row_to_dict(
        connection.execute(
            "SELECT * FROM scientific_development_treasury WHERE treasury_id = ?",
            (SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,),
        ).fetchone()
    )


def _ensure_treasury(connection: Any) -> None:
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
        ON CONFLICT(treasury_id) DO UPDATE SET
            treasury_wallet = excluded.treasury_wallet,
            governance_wallet = excluded.governance_wallet,
            updated_at = excluded.updated_at
        """,
        (
            SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
            now,
            SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET,
            SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET,
            now,
            now,
        ),
    )


def _ensure_epoch(connection: Any, epoch: str, block_height: int, unlock_at: str) -> dict[str, Any]:
    now = utc_now()
    connection.execute(
        """
        INSERT INTO scientific_development_treasury_epochs (
            epoch, start_block, end_block, locked_amount, unlocked_amount,
            claimed_amount, unlock_at, status, created_at, updated_at
        )
        VALUES (?, ?, ?, 0, 0, 0, ?, 'locked', ?, ?)
        ON CONFLICT(epoch) DO NOTHING
        """,
        (epoch, block_height, block_height, unlock_at, now, now),
    )
    return row_to_dict(
        connection.execute(
            "SELECT * FROM scientific_development_treasury_epochs WHERE epoch = ?",
            (epoch,),
        ).fetchone()
    )


def _earliest_locked_unlock_at(connection: Any) -> str | None:
    row = connection.execute(
        """
        SELECT unlock_at
        FROM scientific_development_treasury_epochs
        WHERE status = 'locked'
        AND locked_amount > 0
        ORDER BY unlock_at ASC
        LIMIT 1
        """
    ).fetchone()
    return None if row is None else row["unlock_at"]


def _current_quarter_epoch() -> str:
    now = utc_now_dt()
    quarter = ((now.month - 1) // 3) + 1
    return f"{now.year}-Q{quarter}"


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _clean_address(address: str) -> str:
    value = str(address or "").strip()
    if not value:
        raise TreasuryError(400, "address is required")
    if len(value) > 128:
        raise TreasuryError(400, "address is too long")
    return value


def _balance_amount(connection: Any, account_id: str) -> float:
    row = connection.execute(
        "SELECT balance FROM balances WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    return 0.0 if row is None else round(float(row["balance"]), 8)


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
    amount = round(float(amount), 8)
    _ensure_balance_account(connection, account_id, account_type)
    current = connection.execute(
        "SELECT balance FROM balances WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    balance_after = round(float(current["balance"]) + amount, 8)
    if balance_after < 0:
        raise TreasuryError(409, "treasury operation would create a negative balance")
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
            amount,
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
