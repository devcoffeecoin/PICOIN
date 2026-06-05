from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
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


def summarize_payouts(
    *,
    task_rewards: Iterable[dict[str, Any]],
    share_rows: Iterable[dict[str, Any]],
    worker_rows: Iterable[dict[str, Any]],
    pool_fee_percent: float = 0.0,
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
            "pending_amount": Decimal("0"),
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
                    "pending_amount": Decimal("0"),
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

    payout_workers = [
        {
            **worker,
            "gross_amount": _decimal_to_float(Decimal(worker["gross_amount"])),
            "pool_fee_amount": _decimal_to_float(Decimal(worker["pool_fee_amount"])),
            "pending_amount": _decimal_to_float(Decimal(worker["pending_amount"])),
        }
        for worker in workers.values()
        if Decimal(worker["pending_amount"]) > 0
    ]
    payout_workers.sort(key=lambda item: (-float(item["pending_amount"]), str(item["worker_id"])))

    return {
        "pool_fee_percent": float(pool_fee_percent),
        "gross_total": _decimal_to_float(total_gross),
        "pool_fee_total": _decimal_to_float(total_fee),
        "pending_total": _decimal_to_float(total_pending),
        "workers": payout_workers,
    }


def _decimal_to_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN))
