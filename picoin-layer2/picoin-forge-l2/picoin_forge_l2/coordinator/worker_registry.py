from __future__ import annotations

import json
from pathlib import Path

from picoin_forge_l2.common.constants import DEFAULT_HEARTBEAT_GRACE_SECONDS, MISSED_HEARTBEAT_PENALTY
from picoin_forge_l2.common.models import BenchmarkResult, Heartbeat, WorkerRegistration, WorkerState, WorkerStatus, utc_now

from .storage import CoordinatorStorage


class WorkerRegistry:
    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.storage = CoordinatorStorage(self.state_dir)

    def register(self, registration: WorkerRegistration) -> WorkerState:
        try:
            state = self.get(registration.worker_id)
            state.registration = registration
            event_type = "worker.updated"
        except KeyError:
            state = WorkerState(registration=registration)
            event_type = "worker.registered"
        self.put(state)
        self.storage.record_event(
            event_type,
            registration.worker_id,
            {"wallet": registration.wallet, "status": registration.status.value},
        )
        return state

    def update_benchmark(self, benchmark: BenchmarkResult) -> WorkerState:
        state = self.get(benchmark.worker_id)
        state.benchmark = benchmark
        self.put(state)
        metric = self.storage.record_benchmark_metric(benchmark)
        self.storage.record_event(
            "worker.benchmark",
            benchmark.worker_id,
            {
                "benchmark_score": benchmark.benchmark_score,
                "normalized_score": metric["normalized_score"],
                "result_hash": benchmark.result_hash,
            },
        )
        return state

    def heartbeat(self, heartbeat: Heartbeat) -> WorkerState:
        state = self.get(heartbeat.worker_id)
        state.registration.status = WorkerStatus.ONLINE
        state.last_heartbeat_at = heartbeat.timestamp
        state.uptime_score = min(100.0, state.uptime_score + 1.0)
        self.put(state)
        self.storage.record_event("worker.heartbeat", heartbeat.worker_id, {"status": heartbeat.status.value})
        return state

    def put(self, state: WorkerState) -> None:
        now = utc_now().isoformat()
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO workers (worker_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    state.registration.worker_id,
                    state.model_dump_json(),
                    now,
                ),
            )

    def get(self, worker_id: str) -> WorkerState:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM workers WHERE worker_id = ?", (worker_id,)).fetchone()
        if row is None:
            raise KeyError(f"worker not found: {worker_id}")
        return WorkerState.model_validate(json.loads(row["payload"]))

    def all(self) -> list[WorkerState]:
        with self.storage.connect() as connection:
            rows = connection.execute("SELECT payload FROM workers ORDER BY worker_id ASC").fetchall()
        return [WorkerState.model_validate(json.loads(row["payload"])) for row in rows]

    def penalize_missed_heartbeats(
        self,
        *,
        grace_seconds: int = DEFAULT_HEARTBEAT_GRACE_SECONDS,
        penalty: float = MISSED_HEARTBEAT_PENALTY,
    ) -> list[WorkerState]:
        now = utc_now()
        updated: list[WorkerState] = []
        for state in self.all():
            if state.last_heartbeat_at is None:
                continue
            age = (now - state.last_heartbeat_at).total_seconds()
            if age <= grace_seconds:
                continue
            state.registration.status = WorkerStatus.OFFLINE
            state.penalty_score += penalty
            state.uptime_score = max(0.0, state.uptime_score - penalty)
            state.reliability_score = max(0.0, state.reliability_score - penalty)
            self.put(state)
            self.storage.record_event(
                "worker.missed_heartbeat",
                state.registration.worker_id,
                {
                    "penalty": penalty,
                    "grace_seconds": grace_seconds,
                    "last_heartbeat_at": state.last_heartbeat_at.isoformat() if state.last_heartbeat_at else None,
                },
            )
            updated.append(state)
        return updated
