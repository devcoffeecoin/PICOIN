from __future__ import annotations

import json
from pathlib import Path

from picoin_forge_l2.common.models import (
    WorkloadCreateRequest,
    WorkloadResult,
    WorkloadStatus,
    WorkloadTask,
    utc_now,
)
from picoin_forge_l2.worker.workloads import compute_workload_result_hash, workload_task_id

from .storage import CoordinatorStorage


class WorkloadQueue:
    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.storage = CoordinatorStorage(self.state_dir)

    def create(self, request: WorkloadCreateRequest) -> WorkloadTask:
        now = utc_now()
        expected = compute_workload_result_hash(request.task_type, request.payload)
        task = WorkloadTask(
            task_id=workload_task_id(request.task_type, request.payload, now.isoformat()),
            task_type=request.task_type,
            payload=request.payload,
            expected_result_hash=expected,
            created_at=now,
            updated_at=now,
        )
        self.put(task)
        self.storage.record_event(
            "workload.created",
            task.task_id,
            {
                "task_type": task.task_type.value,
                "requester_wallet": request.requester_wallet,
            },
        )
        return task

    def claim_next(self, worker_id: str) -> WorkloadTask | None:
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM workloads WHERE status = ? ORDER BY updated_at ASC LIMIT 1",
                (WorkloadStatus.QUEUED.value,),
            ).fetchone()
        if row is None:
            return None
        task = WorkloadTask.model_validate(json.loads(row["payload"]))
        task.status = WorkloadStatus.ASSIGNED
        task.assigned_worker_id = worker_id
        task.updated_at = utc_now()
        self.put(task)
        self.storage.record_event("workload.assigned", task.task_id, {"worker_id": worker_id})
        return task

    def submit(self, result: WorkloadResult) -> WorkloadTask:
        task = self.get(result.task_id)
        if task.assigned_worker_id != result.worker_id:
            task.status = WorkloadStatus.FAILED
            task.result_hash = result.result_hash
            task.updated_at = utc_now()
            self.put(task)
            self.storage.record_event("workload.failed", task.task_id, {"reason": "worker_mismatch"})
            return task
        task.result_hash = result.result_hash
        task.status = WorkloadStatus.VERIFIED if result.result_hash == task.expected_result_hash else WorkloadStatus.FAILED
        task.updated_at = utc_now()
        self.put(task)
        self.storage.record_event(
            "workload.verified" if task.status == WorkloadStatus.VERIFIED else "workload.failed",
            task.task_id,
            {
                "worker_id": result.worker_id,
                "result_hash": result.result_hash,
            },
        )
        return task

    def get(self, task_id: str) -> WorkloadTask:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM workloads WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"workload not found: {task_id}")
        return WorkloadTask.model_validate(json.loads(row["payload"]))

    def list(self, limit: int = 100) -> list[WorkloadTask]:
        safe_limit = max(1, min(int(limit), 1000))
        with self.storage.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM workloads ORDER BY updated_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [WorkloadTask.model_validate(json.loads(row["payload"])) for row in rows]

    def put(self, task: WorkloadTask) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO workloads (task_id, status, assigned_worker_id, payload, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = excluded.status,
                    assigned_worker_id = excluded.assigned_worker_id,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    task.task_id,
                    task.status.value,
                    task.assigned_worker_id,
                    task.model_dump_json(),
                    task.updated_at.isoformat(),
                ),
            )
