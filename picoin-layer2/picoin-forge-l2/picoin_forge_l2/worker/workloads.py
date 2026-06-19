from __future__ import annotations

from picoin_forge_l2.common.hashing import hash_json, sha256_text
from picoin_forge_l2.common.models import WorkloadTask, WorkloadType


def compute_workload_result_hash(task_type: WorkloadType, payload: dict) -> str:
    if task_type == WorkloadType.HASH_TEXT:
        return sha256_text(str(payload.get("text", "")))
    raise ValueError(f"unsupported workload type: {task_type}")


def solve_workload(task: WorkloadTask) -> str:
    return compute_workload_result_hash(task.task_type, task.payload)


def workload_task_id(task_type: WorkloadType, payload: dict, created_at: str) -> str:
    return "workload_" + hash_json(
        {
            "task_type": task_type.value,
            "payload": payload,
            "created_at": created_at,
        }
    )[:16]
