from __future__ import annotations

from typing import Any

from app.core.money import to_units, units_from_db, units_to_float
from app.core.settings import BLOCK_MATURITY_DEPTH


def record_miner_block_reward(
    connection: Any,
    *,
    miner_id: str,
    account_id: str,
    account_type: str,
    block_height: int,
    amount: float,
    reason: str,
    related_id: str | None,
    description: str,
    timestamp: str,
) -> dict[str, Any]:
    maturity_depth = max(0, int(BLOCK_MATURITY_DEPTH))
    is_mature = maturity_depth == 0
    status = "mature" if is_mature else "immature"
    matures_at_height = block_height if is_mature else block_height + maturity_depth
    matured_at = timestamp if is_mature else None

    _ensure_balance_account(connection, account_id, account_type, timestamp)
    cursor = connection.execute(
        """
        INSERT INTO rewards (
            miner_id, block_height, amount, amount_units, account_id, account_type,
            status, matures_at_height, matured_at, related_id, reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            miner_id,
            block_height,
            amount,
            to_units(amount),
            account_id,
            account_type,
            status,
            matures_at_height,
            matured_at,
            related_id,
            reason,
            timestamp,
        ),
    )
    reward_id = int(cursor.lastrowid)
    if is_mature:
        _apply_reward_ledger_entry(
            connection,
            account_id=account_id,
            account_type=account_type,
            amount=amount,
            block_height=block_height,
            related_id=related_id,
            description=description,
            timestamp=timestamp,
        )

    return {
        "reward_id": reward_id,
        "status": status,
        "matures_at_height": matures_at_height,
        "matured": is_mature,
    }


def mature_block_rewards(connection: Any, *, current_height: int, timestamp: str) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT *
        FROM rewards
        WHERE status = 'immature'
          AND matures_at_height IS NOT NULL
          AND matures_at_height <= ?
        ORDER BY matures_at_height ASC, id ASC
        """,
        (current_height,),
    ).fetchall()
    matured_count = 0
    matured_units = 0
    for row in rows:
        reward_id = int(row["id"])
        account_id = row["account_id"] or row["miner_id"]
        account_type = row["account_type"] or ("wallet" if str(account_id).startswith("PI") else "miner")
        amount_units = int(row["amount_units"] or to_units(row["amount"]))
        related_id = row["related_id"] or f"reward:{reward_id}"
        existing = connection.execute(
            """
            SELECT 1
            FROM ledger_entries
            WHERE entry_type = 'block_reward'
              AND account_id = ?
              AND related_id = ?
              AND amount_units = ?
            LIMIT 1
            """,
            (account_id, related_id, amount_units),
        ).fetchone()
        if existing is None:
            _apply_reward_ledger_entry(
                connection,
                account_id=account_id,
                account_type=account_type,
                amount=units_to_float(amount_units),
                block_height=current_height,
                related_id=related_id,
                description=f"matured miner block reward from block {row['block_height']}",
                timestamp=timestamp,
            )
        connection.execute(
            """
            UPDATE rewards
            SET status = 'mature', matured_at = ?
            WHERE id = ?
            """,
            (timestamp, reward_id),
        )
        matured_count += 1
        matured_units += amount_units

    return {
        "matured_count": matured_count,
        "matured_amount": units_to_float(matured_units),
        "matured_units": matured_units,
    }


def immature_reward_total_for_account(connection: Any, account_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT COALESCE(SUM(amount_units), 0) AS total_units, COUNT(*) AS count
        FROM rewards
        WHERE account_id = ?
          AND status = 'immature'
        """,
        (account_id,),
    ).fetchone()
    total_units = int(row["total_units"] or 0)
    return {
        "immature_rewards": units_to_float(total_units),
        "immature_rewards_units": total_units,
        "immature_reward_count": int(row["count"] or 0),
    }


def _ensure_balance_account(connection: Any, account_id: str, account_type: str, timestamp: str) -> None:
    connection.execute(
        """
        INSERT INTO balances (account_id, account_type, balance, balance_units, updated_at)
        VALUES (?, ?, 0, 0, ?)
        ON CONFLICT(account_id) DO NOTHING
        """,
        (account_id, account_type, timestamp),
    )


def _apply_reward_ledger_entry(
    connection: Any,
    *,
    account_id: str,
    account_type: str,
    amount: float,
    block_height: int,
    related_id: str | None,
    description: str,
    timestamp: str,
) -> None:
    _ensure_balance_account(connection, account_id, account_type, timestamp)
    current = connection.execute(
        "SELECT balance, balance_units FROM balances WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    amount_units = to_units(amount)
    current_units = units_from_db(current["balance"], current["balance_units"])
    balance_after_units = current_units + amount_units
    balance_after = units_to_float(balance_after_units)
    connection.execute(
        "UPDATE balances SET balance = ?, balance_units = ?, updated_at = ? WHERE account_id = ?",
        (balance_after, balance_after_units, timestamp, account_id),
    )
    connection.execute(
        """
        INSERT INTO ledger_entries (
            account_id, account_type, amount, amount_units, balance_after, balance_after_units,
            entry_type, block_height, related_id, description, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'block_reward', ?, ?, ?, ?)
        """,
        (
            account_id,
            account_type,
            units_to_float(amount_units),
            amount_units,
            balance_after,
            balance_after_units,
            block_height,
            related_id,
            description,
            timestamp,
        ),
    )
