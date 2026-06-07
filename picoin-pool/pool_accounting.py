from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any


@dataclass(frozen=True)
class WorkChunk:
    range_start: int
    range_end: int

    @property
    def units(self) -> int:
        return self.range_end - self.range_start + 1


def split_range(range_start: int, range_end: int, chunk_size: int) -> list[WorkChunk]:
    if range_start < 1:
        raise ValueError("range_start must be >= 1")
    if range_end < range_start:
        raise ValueError("range_end must be >= range_start")
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")

    chunks: list[WorkChunk] = []
    current = range_start
    while current <= range_end:
        chunk_end = min(range_end, current + chunk_size - 1)
        chunks.append(WorkChunk(current, chunk_end))
        current = chunk_end + 1
    return chunks


def split_range_balanced(range_start: int, range_end: int, target_chunks: int) -> list[WorkChunk]:
    if range_start < 1:
        raise ValueError("range_start must be >= 1")
    if range_end < range_start:
        raise ValueError("range_end must be >= range_start")

    units = range_end - range_start + 1
    chunk_count = max(1, min(int(target_chunks or 1), units))
    base_units = units // chunk_count
    extra_units = units % chunk_count

    chunks: list[WorkChunk] = []
    current = range_start
    for index in range(chunk_count):
        chunk_units = base_units + (1 if index < extra_units else 0)
        chunk_end = current + chunk_units - 1
        chunks.append(WorkChunk(current, chunk_end))
        current = chunk_end + 1
    return chunks


def assemble_segment(range_start: int, range_end: int, chunks: Iterable[dict[str, Any]]) -> str:
    expected = range_start
    pieces: list[str] = []

    ordered = sorted(chunks, key=lambda item: int(item["range_start"]))
    for chunk in ordered:
        chunk_start = int(chunk["range_start"])
        chunk_end = int(chunk["range_end"])
        segment = str(chunk["segment"])
        if chunk_start != expected:
            raise ValueError(f"missing segment data at position {expected}")
        if chunk_end < chunk_start:
            raise ValueError("chunk range_end must be >= range_start")
        if len(segment) != chunk_end - chunk_start + 1:
            raise ValueError(f"segment length mismatch for {chunk_start}..{chunk_end}")
        pieces.append(segment)
        expected = chunk_end + 1

    if expected != range_end + 1:
        raise ValueError(f"missing segment data at position {expected}")
    return "".join(pieces)


def summarize_shares(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    totals: dict[str, int] = {}
    for row in rows:
        worker_id = str(row["worker_id"])
        totals[worker_id] = totals.get(worker_id, 0) + int(row.get("units") or 0)

    total_units = sum(totals.values())
    return {
        worker_id: {
            "units": units,
            "fraction": (units / total_units) if total_units else 0.0,
        }
        for worker_id, units in sorted(totals.items())
    }


def filter_shares_by_window(
    rows: Iterable[dict[str, Any]],
    *,
    start_at: str | None = None,
    end_at: str | None = None,
) -> list[dict[str, Any]]:
    start_ts = _timestamp(start_at)
    end_ts = _timestamp(end_at)
    windowed: list[dict[str, Any]] = []
    for row in rows:
        created_ts = _timestamp(row.get("created_at"))
        if created_ts <= 0:
            continue
        if start_ts > 0 and created_ts <= start_ts:
            continue
        if end_ts > 0 and created_ts > end_ts:
            continue
        windowed.append(dict(row))
    return windowed


def summarize_payouts(
    *,
    task_rewards: Iterable[dict[str, Any]],
    share_rows: Iterable[dict[str, Any]],
    worker_rows: Iterable[dict[str, Any]],
    payout_rows: Iterable[dict[str, Any]] | None = None,
    pool_fee_percent: float = 0.0,
    min_payout_amount: float = 0.0,
) -> dict[str, Any]:
    rewards = {
        str(row["pool_task_id"]): Decimal(str(row.get("reward") or "0"))
        for row in task_rewards
        if Decimal(str(row.get("reward") or "0")) > 0
    }
    workers = {
        str(row["worker_id"]): {
            "worker_id": str(row["worker_id"]),
            "name": row.get("name"),
            "payout_address": row.get("payout_address"),
            "units": 0,
            "accepted_tasks": 0,
            "gross_amount": Decimal("0"),
            "pool_fee_amount": Decimal("0"),
            "paid_amount": Decimal("0"),
            "pending_amount": Decimal("0"),
            "payable": False,
        }
        for row in worker_rows
    }

    shares_by_task: dict[str, dict[str, int]] = {}
    for row in share_rows:
        pool_task_id = str(row["pool_task_id"])
        if pool_task_id not in rewards:
            continue
        worker_id = str(row["worker_id"])
        units = int(row.get("units") or 0)
        if units <= 0:
            continue
        shares_by_task.setdefault(pool_task_id, {})
        shares_by_task[pool_task_id][worker_id] = shares_by_task[pool_task_id].get(worker_id, 0) + units

    fee_rate = Decimal(str(max(0.0, min(100.0, float(pool_fee_percent))))) / Decimal("100")
    total_gross = Decimal("0")
    total_fee = Decimal("0")
    total_pending = Decimal("0")

    for pool_task_id, worker_units in shares_by_task.items():
        reward = rewards[pool_task_id]
        task_units = sum(worker_units.values())
        if task_units <= 0:
            continue
        for worker_id, units in worker_units.items():
            worker = workers.setdefault(
                worker_id,
                {
                    "worker_id": worker_id,
                    "name": None,
                    "payout_address": None,
                    "units": 0,
                    "accepted_tasks": 0,
                    "gross_amount": Decimal("0"),
                    "pool_fee_amount": Decimal("0"),
                    "paid_amount": Decimal("0"),
                    "pending_amount": Decimal("0"),
                    "payable": False,
                },
            )
            gross = reward * Decimal(units) / Decimal(task_units)
            fee = gross * fee_rate
            pending = gross - fee
            worker["units"] = int(worker["units"]) + units
            worker["accepted_tasks"] = int(worker["accepted_tasks"]) + 1
            worker["gross_amount"] = Decimal(worker["gross_amount"]) + gross
            worker["pool_fee_amount"] = Decimal(worker["pool_fee_amount"]) + fee
            worker["pending_amount"] = Decimal(worker["pending_amount"]) + pending
            total_gross += gross
            total_fee += fee
            total_pending += pending

    total_paid = Decimal("0")
    for row in payout_rows or []:
        worker_id = str(row.get("worker_id") or "")
        if not worker_id:
            continue
        try:
            amount = Decimal(str(row.get("amount") or "0"))
        except Exception:
            continue
        if amount <= 0:
            continue
        worker = workers.setdefault(
            worker_id,
            {
                "worker_id": worker_id,
                "name": row.get("name"),
                "payout_address": row.get("payout_address"),
                "units": 0,
                "accepted_tasks": 0,
                "gross_amount": Decimal("0"),
                "pool_fee_amount": Decimal("0"),
                "paid_amount": Decimal("0"),
                "pending_amount": Decimal("0"),
                "payable": False,
            },
        )
        worker["paid_amount"] = Decimal(worker["paid_amount"]) + amount
        worker["pending_amount"] = max(Decimal("0"), Decimal(worker["pending_amount"]) - amount)
        total_paid += amount

    total_pending = sum((Decimal(worker["pending_amount"]) for worker in workers.values()), Decimal("0"))
    min_payout = Decimal(str(max(0.0, float(min_payout_amount or 0.0))))
    payout_workers = [
        {
            **worker,
            "gross_amount": _decimal_to_float(Decimal(worker["gross_amount"])),
            "pool_fee_amount": _decimal_to_float(Decimal(worker["pool_fee_amount"])),
            "paid_amount": _decimal_to_float(Decimal(worker["paid_amount"])),
            "pending_amount": _decimal_to_float(Decimal(worker["pending_amount"])),
            "payable": Decimal(worker["pending_amount"]) >= min_payout if min_payout > 0 else Decimal(worker["pending_amount"]) > 0,
        }
        for worker in workers.values()
        if Decimal(worker["pending_amount"]) > 0
    ]
    payout_workers.sort(key=lambda item: (-float(item["pending_amount"]), str(item["worker_id"])))

    return {
        "pool_fee_percent": float(pool_fee_percent),
        "min_payout_amount": float(min_payout),
        "gross_total": _decimal_to_float(total_gross),
        "pool_fee_total": _decimal_to_float(total_fee),
        "paid_total": _decimal_to_float(total_paid),
        "pending_total": _decimal_to_float(total_pending),
        "workers": payout_workers,
    }


def summarize_round_window_payouts(
    *,
    task_rewards: Iterable[dict[str, Any]],
    share_rows: Iterable[dict[str, Any]],
    worker_rows: Iterable[dict[str, Any]],
    payout_rows: Iterable[dict[str, Any]] | None = None,
    pool_fee_percent: float = 0.0,
    min_payout_amount: float = 0.0,
) -> dict[str, Any]:
    rewards = []
    for row in task_rewards:
        reward = Decimal(str(row.get("reward") or "0"))
        if reward <= 0:
            continue
        completed_at = str(row.get("completed_at") or "")
        completed_ts = _timestamp(completed_at)
        if completed_ts <= 0:
            continue
        rewards.append(
            {
                **dict(row),
                "_reward": reward,
                "_completed_ts": completed_ts,
                "completed_at": completed_at,
            }
        )
    rewards.sort(key=lambda row: (float(row["_completed_ts"]), str(row.get("pool_task_id") or "")))

    shares = []
    for row in share_rows:
        units = int(row.get("units") or 0)
        created_ts = _timestamp(row.get("created_at"))
        if units <= 0 or created_ts <= 0:
            continue
        shares.append({**dict(row), "_created_ts": created_ts, "units": units})

    workers = {
        str(row["worker_id"]): {
            "worker_id": str(row["worker_id"]),
            "name": row.get("name"),
            "payout_address": row.get("payout_address"),
            "units": 0,
            "accepted_tasks": 0,
            "gross_amount": Decimal("0"),
            "pool_fee_amount": Decimal("0"),
            "paid_amount": Decimal("0"),
            "pending_amount": Decimal("0"),
            "payable": False,
        }
        for row in worker_rows
    }

    fee_rate = Decimal(str(max(0.0, min(100.0, float(pool_fee_percent))))) / Decimal("100")
    total_gross = Decimal("0")
    total_fee = Decimal("0")
    rounds: list[dict[str, Any]] = []
    previous_completed_at: str | None = None
    previous_completed_ts = 0.0

    for reward_row in rewards:
        completed_ts = float(reward_row["_completed_ts"])
        round_shares: dict[str, int] = {}
        for share in shares:
            created_ts = float(share["_created_ts"])
            if previous_completed_ts > 0 and created_ts <= previous_completed_ts:
                continue
            if created_ts > completed_ts:
                continue
            worker_id = str(share["worker_id"])
            round_shares[worker_id] = round_shares.get(worker_id, 0) + int(share["units"])

        round_units = sum(round_shares.values())
        rounds.append(
            {
                "pool_task_id": reward_row.get("pool_task_id"),
                "mainnet_task_id": reward_row.get("mainnet_task_id"),
                "height": reward_row.get("height"),
                "block_hash": reward_row.get("block_hash"),
                "reward": _decimal_to_float(Decimal(reward_row["_reward"])),
                "round_start_at": previous_completed_at,
                "round_end_at": reward_row.get("completed_at"),
                "total_units": round_units,
                "worker_count": len(round_shares),
            }
        )
        if round_units > 0:
            reward = Decimal(reward_row["_reward"])
            for worker_id, units in round_shares.items():
                worker = workers.setdefault(
                    worker_id,
                    {
                        "worker_id": worker_id,
                        "name": None,
                        "payout_address": None,
                        "units": 0,
                        "accepted_tasks": 0,
                        "gross_amount": Decimal("0"),
                        "pool_fee_amount": Decimal("0"),
                        "paid_amount": Decimal("0"),
                        "pending_amount": Decimal("0"),
                        "payable": False,
                    },
                )
                gross = reward * Decimal(units) / Decimal(round_units)
                fee = gross * fee_rate
                pending = gross - fee
                worker["units"] = int(worker["units"]) + units
                worker["accepted_tasks"] = int(worker["accepted_tasks"]) + 1
                worker["gross_amount"] = Decimal(worker["gross_amount"]) + gross
                worker["pool_fee_amount"] = Decimal(worker["pool_fee_amount"]) + fee
                worker["pending_amount"] = Decimal(worker["pending_amount"]) + pending
                total_gross += gross
                total_fee += fee

        previous_completed_at = str(reward_row.get("completed_at") or "")
        previous_completed_ts = completed_ts

    total_paid = Decimal("0")
    for row in payout_rows or []:
        worker_id = str(row.get("worker_id") or "")
        if not worker_id:
            continue
        try:
            amount = Decimal(str(row.get("amount") or "0"))
        except Exception:
            continue
        if amount <= 0:
            continue
        worker = workers.setdefault(
            worker_id,
            {
                "worker_id": worker_id,
                "name": row.get("name"),
                "payout_address": row.get("payout_address"),
                "units": 0,
                "accepted_tasks": 0,
                "gross_amount": Decimal("0"),
                "pool_fee_amount": Decimal("0"),
                "paid_amount": Decimal("0"),
                "pending_amount": Decimal("0"),
                "payable": False,
            },
        )
        worker["paid_amount"] = Decimal(worker["paid_amount"]) + amount
        worker["pending_amount"] = max(Decimal("0"), Decimal(worker["pending_amount"]) - amount)
        total_paid += amount

    total_pending = sum((Decimal(worker["pending_amount"]) for worker in workers.values()), Decimal("0"))
    min_payout = Decimal(str(max(0.0, float(min_payout_amount or 0.0))))
    payout_workers = [
        {
            **worker,
            "gross_amount": _decimal_to_float(Decimal(worker["gross_amount"])),
            "pool_fee_amount": _decimal_to_float(Decimal(worker["pool_fee_amount"])),
            "paid_amount": _decimal_to_float(Decimal(worker["paid_amount"])),
            "pending_amount": _decimal_to_float(Decimal(worker["pending_amount"])),
            "payable": Decimal(worker["pending_amount"]) >= min_payout if min_payout > 0 else Decimal(worker["pending_amount"]) > 0,
        }
        for worker in workers.values()
        if Decimal(worker["pending_amount"]) > 0
    ]
    payout_workers.sort(key=lambda item: (-float(item["pending_amount"]), str(item["worker_id"])))

    net_total = total_gross - total_fee
    top_up = max(Decimal("0"), total_paid + total_pending - net_total)
    return {
        "accounting_mode": "round_window",
        "pool_fee_percent": float(pool_fee_percent),
        "min_payout_amount": float(min_payout),
        "gross_total": _decimal_to_float(total_gross),
        "pool_fee_total": _decimal_to_float(total_fee),
        "net_total": _decimal_to_float(net_total),
        "paid_total": _decimal_to_float(total_paid),
        "pending_total": _decimal_to_float(total_pending),
        "operator_top_up_total": _decimal_to_float(top_up),
        "rounds": rounds[-20:][::-1],
        "workers": payout_workers,
    }


def _decimal_to_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN))


def _timestamp(value: Any) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
