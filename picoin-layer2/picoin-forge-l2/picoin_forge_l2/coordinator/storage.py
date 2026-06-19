from __future__ import annotations

import sqlite3
import os
from pathlib import Path
from typing import Any

from picoin_forge_l2.common.hashing import hash_json
from picoin_forge_l2.common.hashing import sha256_text
from picoin_forge_l2.common.models import BenchmarkResult, ChallengeType, CoordinatorEvent, utc_now


DEFAULT_BENCHMARK_NORMALIZATION_CAPS = {
    "cpu_score": 1000.0,
    "gpu_score": 1000.0,
    "ram_score": 10000.0,
    "io_score": 10000.0,
}


class CoordinatorStorage:
    """Small SQLite store for the L2 MVP.

    The store is intentionally local-only. It does not touch Picoin L1.
    """

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "coordinator.sqlite3"
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def init_db(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS challenges (
                    challenge_id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_challenges_worker_id
                    ON challenges(worker_id);

                CREATE TABLE IF NOT EXISTS epoch_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    current_epoch INTEGER NOT NULL
                );

                INSERT OR IGNORE INTO epoch_state (id, current_epoch)
                VALUES (1, 0);

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    subject_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_type_created
                    ON events(event_type, created_at);

                CREATE INDEX IF NOT EXISTS idx_events_subject_created
                    ON events(subject_id, created_at);

                CREATE TABLE IF NOT EXISTS benchmark_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    worker_id TEXT NOT NULL,
                    cpu_score REAL NOT NULL,
                    gpu_score REAL NOT NULL,
                    ram_score REAL NOT NULL,
                    io_score REAL NOT NULL,
                    benchmark_score REAL NOT NULL,
                    normalized_score REAL NOT NULL,
                    result_hash TEXT NOT NULL,
                    measured_at TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_benchmark_metrics_worker_created
                    ON benchmark_metrics(worker_id, created_at);

                CREATE TABLE IF NOT EXISTS challenge_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    challenge_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    challenge_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    difficulty INTEGER NOT NULL,
                    passed INTEGER NOT NULL,
                    elapsed_ms REAL NOT NULL,
                    reliability_delta REAL NOT NULL,
                    penalty_delta REAL NOT NULL,
                    result_hash TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_challenge_metrics_worker_created
                    ON challenge_metrics(worker_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_challenge_metrics_challenge_id
                    ON challenge_metrics(challenge_id);

                CREATE TABLE IF NOT EXISTS worker_signature_replay (
                    signature_hash TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    request_timestamp TEXT NOT NULL,
                    seen_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_worker_signature_replay_worker_seen
                    ON worker_signature_replay(worker_id, seen_at);

                CREATE TABLE IF NOT EXISTS workloads (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    assigned_worker_id TEXT,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_workloads_status_updated
                    ON workloads(status, updated_at);
                """
            )

    def record_event(self, event_type: str, subject_id: str | None = None, payload: dict[str, Any] | None = None) -> CoordinatorEvent:
        event_payload = payload or {}
        created_at = utc_now()
        event_id = "event_" + hash_json(
            {
                "event_type": event_type,
                "subject_id": subject_id,
                "payload": event_payload,
                "created_at": created_at.isoformat(),
            }
        )[:20]
        event = CoordinatorEvent(
            event_id=event_id,
            event_type=event_type,
            subject_id=subject_id,
            payload=event_payload,
            created_at=created_at,
        )
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO events (event_id, event_type, subject_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.subject_id,
                    event.model_dump_json(),
                    event.created_at.isoformat(),
                ),
            )
        return event

    def list_events(self, limit: int = 100) -> list[CoordinatorEvent]:
        safe_limit = max(1, min(int(limit), 1000))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM events ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [CoordinatorEvent.model_validate_json(row["payload"]) for row in rows]

    def record_benchmark_metric(self, benchmark: BenchmarkResult) -> dict[str, Any]:
        created_at = utc_now().isoformat()
        normalized_score = normalize_benchmark_score(benchmark)
        row = {
            "worker_id": benchmark.worker_id,
            "cpu_score": benchmark.cpu_score,
            "gpu_score": benchmark.gpu_score,
            "ram_score": benchmark.ram_score,
            "io_score": benchmark.io_score,
            "benchmark_score": benchmark.benchmark_score,
            "normalized_score": normalized_score,
            "result_hash": benchmark.result_hash,
            "measured_at": benchmark.measured_at.isoformat(),
            "details": benchmark.details,
            "created_at": created_at,
        }
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO benchmark_metrics (
                    worker_id,
                    cpu_score,
                    gpu_score,
                    ram_score,
                    io_score,
                    benchmark_score,
                    normalized_score,
                    result_hash,
                    measured_at,
                    details,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    benchmark.worker_id,
                    benchmark.cpu_score,
                    benchmark.gpu_score,
                    benchmark.ram_score,
                    benchmark.io_score,
                    benchmark.benchmark_score,
                    normalized_score,
                    benchmark.result_hash,
                    benchmark.measured_at.isoformat(),
                    hash_json(benchmark.details),
                    created_at,
                ),
            )
            row["id"] = cursor.lastrowid
        return row

    def list_benchmark_metrics(self, *, worker_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        query = "SELECT * FROM benchmark_metrics"
        params: list[Any] = []
        if worker_id:
            query += " WHERE worker_id = ?"
            params.append(worker_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [benchmark_metric_row_to_dict(row) for row in rows]

    def record_challenge_metric(
        self,
        *,
        challenge_id: str,
        worker_id: str,
        challenge_type: ChallengeType,
        status: str,
        difficulty: int,
        passed: bool,
        elapsed_ms: float = 0.0,
        reliability_delta: float = 0.0,
        penalty_delta: float = 0.0,
        result_hash: str | None = None,
    ) -> dict[str, Any]:
        created_at = utc_now().isoformat()
        row = {
            "challenge_id": challenge_id,
            "worker_id": worker_id,
            "challenge_type": challenge_type.value,
            "status": status,
            "difficulty": difficulty,
            "passed": bool(passed),
            "elapsed_ms": elapsed_ms,
            "reliability_delta": reliability_delta,
            "penalty_delta": penalty_delta,
            "result_hash": result_hash,
            "created_at": created_at,
        }
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO challenge_metrics (
                    challenge_id,
                    worker_id,
                    challenge_type,
                    status,
                    difficulty,
                    passed,
                    elapsed_ms,
                    reliability_delta,
                    penalty_delta,
                    result_hash,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    challenge_id,
                    worker_id,
                    challenge_type.value,
                    status,
                    difficulty,
                    1 if passed else 0,
                    elapsed_ms,
                    reliability_delta,
                    penalty_delta,
                    result_hash,
                    created_at,
                ),
            )
            row["id"] = cursor.lastrowid
        return row

    def list_challenge_metrics(self, *, worker_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        query = "SELECT * FROM challenge_metrics"
        params: list[Any] = []
        if worker_id:
            query += " WHERE worker_id = ?"
            params.append(worker_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [challenge_metric_row_to_dict(row) for row in rows]

    def record_worker_signature_once(self, *, worker_id: str, signature: str, request_timestamp: str) -> bool:
        signature_hash = sha256_text(signature)
        seen_at = utc_now().isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO worker_signature_replay (
                    signature_hash,
                    worker_id,
                    request_timestamp,
                    seen_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (signature_hash, worker_id, request_timestamp, seen_at),
            )
        return cursor.rowcount == 1


def normalize_benchmark_component(value: float, cap: float) -> float:
    if cap <= 0:
        return 0.0
    return round(min(max(float(value), 0.0) / cap, 1.0) * 100.0, 8)


def normalize_benchmark_score(benchmark: BenchmarkResult) -> float:
    caps = benchmark_normalization_caps()
    components = [
        normalize_benchmark_component(benchmark.cpu_score, caps["cpu_score"]),
        normalize_benchmark_component(benchmark.gpu_score, caps["gpu_score"]),
        normalize_benchmark_component(benchmark.ram_score, caps["ram_score"]),
        normalize_benchmark_component(benchmark.io_score, caps["io_score"]),
    ]
    return round(sum(components) / len(components), 8)


def benchmark_normalization_caps() -> dict[str, float]:
    return {
        "cpu_score": _env_float("PICOIN_FORGE_CPU_SCORE_CAP", DEFAULT_BENCHMARK_NORMALIZATION_CAPS["cpu_score"]),
        "gpu_score": _env_float("PICOIN_FORGE_GPU_SCORE_CAP", DEFAULT_BENCHMARK_NORMALIZATION_CAPS["gpu_score"]),
        "ram_score": _env_float("PICOIN_FORGE_RAM_SCORE_CAP", DEFAULT_BENCHMARK_NORMALIZATION_CAPS["ram_score"]),
        "io_score": _env_float("PICOIN_FORGE_IO_SCORE_CAP", DEFAULT_BENCHMARK_NORMALIZATION_CAPS["io_score"]),
    }


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


def benchmark_metric_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "worker_id": row["worker_id"],
        "cpu_score": row["cpu_score"],
        "gpu_score": row["gpu_score"],
        "ram_score": row["ram_score"],
        "io_score": row["io_score"],
        "benchmark_score": row["benchmark_score"],
        "normalized_score": row["normalized_score"],
        "result_hash": row["result_hash"],
        "measured_at": row["measured_at"],
        "details_hash": row["details"],
        "created_at": row["created_at"],
    }


def challenge_metric_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "challenge_id": row["challenge_id"],
        "worker_id": row["worker_id"],
        "challenge_type": row["challenge_type"],
        "status": row["status"],
        "difficulty": row["difficulty"],
        "passed": bool(row["passed"]),
        "elapsed_ms": row["elapsed_ms"],
        "reliability_delta": row["reliability_delta"],
        "penalty_delta": row["penalty_delta"],
        "result_hash": row["result_hash"],
        "created_at": row["created_at"],
    }
