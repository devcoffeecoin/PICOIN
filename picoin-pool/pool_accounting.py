from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
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

