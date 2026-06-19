from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

from picoin_forge_l2.common.constants import (
    DEFAULT_AI_ACCESS_MIN_STAKE_PI,
    DEFAULT_AI_REQUEST_LEASE_SECONDS,
    DEFAULT_AI_REQUEST_MAX_ASSIGNMENTS,
)
from picoin_forge_l2.common.hashing import hash_json, sha256_text
from picoin_forge_l2.common.models import (
    AIInferenceCreateRequest,
    AIInferenceRequest,
    AIInferenceResult,
    AIRequestStatus,
    WorkerState,
    utc_now,
)

from .storage import CoordinatorStorage
from .worker_registry import WorkerRegistry


class AIAccessQueue:
    def __init__(self, state_dir: str | Path, registry: WorkerRegistry):
        self.state_dir = Path(state_dir)
        self.storage = CoordinatorStorage(self.state_dir)
        self.registry = registry

    def create(self, request: AIInferenceCreateRequest) -> AIInferenceRequest:
        required_stake = ai_access_min_stake_pi()
        if request.stake_snapshot_pi < required_stake:
            raise PermissionError(
                f"stake_snapshot_pi {request.stake_snapshot_pi} below required AI access stake {required_stake}"
            )
        now = utc_now()
        prompt_hash = sha256_text(request.prompt)
        item = AIInferenceRequest(
            request_id="ai_req_" + hash_json(
                {
                    "requester_wallet": request.requester_wallet,
                    "prompt_hash": prompt_hash,
                    "created_at": now.isoformat(),
                }
            )[:18],
            requester_wallet=request.requester_wallet.strip().upper(),
            stake_snapshot_pi=request.stake_snapshot_pi,
            required_stake_pi=required_stake,
            prompt=request.prompt,
            prompt_hash=prompt_hash,
            required_capabilities=sorted({capability.strip() for capability in request.required_capabilities if capability.strip()}),
            model_hint=request.model_hint,
            min_parameter_count_b=request.min_parameter_count_b,
            min_context_tokens=request.min_context_tokens,
            preferred_provider=request.preferred_provider,
            max_tokens=request.max_tokens,
            store_output=request.store_output,
            created_at=now,
            updated_at=now,
        )
        self.put(item)
        self.storage.record_event(
            "ai_request.created",
            item.request_id,
            {
                "requester_wallet": item.requester_wallet,
                "prompt_hash": item.prompt_hash,
                "required_capabilities": item.required_capabilities,
                "model_hint": item.model_hint,
                "min_parameter_count_b": item.min_parameter_count_b,
                "min_context_tokens": item.min_context_tokens,
                "preferred_provider": item.preferred_provider,
                "stake_snapshot_pi": item.stake_snapshot_pi,
                "required_stake_pi": item.required_stake_pi,
                "store_output": item.store_output,
                "no_per_task_payment": True,
            },
        )
        return item

    def claim_next(self, worker_id: str) -> AIInferenceRequest | None:
        self.release_expired_assignments()
        worker = self.registry.get(worker_id)
        if not worker_can_serve_ai(worker):
            return None
        with self.storage.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM ai_requests WHERE status = ? ORDER BY updated_at ASC LIMIT 100",
                (AIRequestStatus.QUEUED.value,),
            ).fetchall()
        items = [AIInferenceRequest.model_validate(json.loads(row["payload"])) for row in rows]
        items.sort(key=ai_request_priority_key)
        for item in items:
            if not worker_matches_request(worker, item):
                continue
            selected = self.select_worker_for_request(item)
            if selected is None or selected["worker_id"] != worker_id:
                continue
            now = utc_now()
            item.status = AIRequestStatus.ASSIGNED
            item.assigned_worker_id = worker_id
            item.model_profile = worker.registration.ai_model_profile
            item.assigned_at = now
            item.lease_expires_at = now + timedelta(seconds=ai_request_lease_seconds())
            item.assignment_attempts += 1
            if worker_id not in item.assignment_history:
                item.assignment_history.append(worker_id)
            item.failure_reason = None
            item.updated_at = now
            self.put(item)
            self.storage.record_event(
                "ai_request.assigned",
                item.request_id,
                {
                    "worker_id": worker_id,
                    "ai_model_score": worker.ai_model_score,
                    "model_name": worker.registration.ai_model_profile.model_name
                    if worker.registration.ai_model_profile
                    else None,
                    "assignment_attempts": item.assignment_attempts,
                    "assignment_history": item.assignment_history,
                    "lease_expires_at": item.lease_expires_at.isoformat() if item.lease_expires_at else None,
                },
            )
            return item
        return None

    def candidate_workers_for_request(self, item: AIInferenceRequest) -> list[dict]:
        active_load = self.active_ai_load_by_worker()
        candidates = []
        for worker in self.registry.all():
            if not worker_matches_request(worker, item):
                continue
            candidates.append(ai_route_candidate(worker, item, active_load.get(worker.registration.worker_id, 0)))
        attempted_workers = set(item.assignment_history)
        fresh_candidates = [candidate for candidate in candidates if candidate["worker_id"] not in attempted_workers]
        if fresh_candidates:
            candidates = fresh_candidates
        return sorted(
            candidates,
            key=lambda candidate: (
                -candidate["routing_score"],
                candidate["active_requests"],
                candidate["worker_id"],
            ),
        )

    def select_worker_for_request(self, item: AIInferenceRequest) -> dict | None:
        candidates = self.candidate_workers_for_request(item)
        return candidates[0] if candidates else None

    def active_ai_load_by_worker(self) -> dict[str, int]:
        with self.storage.connect() as connection:
            rows = connection.execute(
                """
                SELECT assigned_worker_id, COUNT(*) AS active_count
                FROM ai_requests
                WHERE status = ?
                  AND assigned_worker_id IS NOT NULL
                GROUP BY assigned_worker_id
                """,
                (AIRequestStatus.ASSIGNED.value,),
            ).fetchall()
        return {str(row["assigned_worker_id"]): int(row["active_count"]) for row in rows}

    def release_expired_assignments(self) -> list[AIInferenceRequest]:
        now = utc_now()
        expired: list[AIInferenceRequest] = []
        with self.storage.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM ai_requests WHERE status = ?",
                (AIRequestStatus.ASSIGNED.value,),
            ).fetchall()
        for row in rows:
            item = AIInferenceRequest.model_validate(json.loads(row["payload"]))
            if item.lease_expires_at is None or item.lease_expires_at > now:
                continue
            expired.append(self._release_expired_assignment(item, now=now))
        return expired

    def submit(self, result: AIInferenceResult) -> AIInferenceRequest:
        item = self.get(result.request_id)
        if item.status == AIRequestStatus.ASSIGNED and item.lease_expires_at and result.submitted_at > item.lease_expires_at:
            return self._release_expired_assignment(item, now=result.submitted_at)
        if item.assigned_worker_id != result.worker_id:
            item.status = AIRequestStatus.FAILED
            item.output_hash = sha256_text(result.output)
            item.failure_reason = "worker_mismatch"
            item.updated_at = utc_now()
            self.put(item)
            self.storage.record_event("ai_request.failed", item.request_id, {"reason": "worker_mismatch"})
            return item
        item.output = result.output if item.store_output else None
        item.output_hash = sha256_text(result.output)
        item.receipt_hash = ai_inference_receipt_hash(item, result)
        item.status = AIRequestStatus.VERIFIED
        item.failure_reason = None
        item.updated_at = utc_now()
        self.put(item)
        self.storage.record_event(
            "ai_request.verified",
            item.request_id,
            {
                "worker_id": result.worker_id,
                "prompt_hash": item.prompt_hash,
                "output_hash": item.output_hash,
                "receipt_hash": item.receipt_hash,
                "no_l1_transaction_created": True,
                "no_per_task_payment": True,
            },
        )
        return item

    def cancel(self, request_id: str) -> AIInferenceRequest:
        item = self.get(request_id)
        if item.status == AIRequestStatus.VERIFIED:
            raise ValueError("verified AI requests cannot be canceled")
        if item.status in {AIRequestStatus.FAILED, AIRequestStatus.CANCELED}:
            return item
        last_worker_id = item.assigned_worker_id
        item.status = AIRequestStatus.CANCELED
        item.assigned_worker_id = None
        item.assigned_at = None
        item.lease_expires_at = None
        item.failure_reason = "requester_canceled"
        item.updated_at = utc_now()
        self.put(item)
        self.storage.record_event(
            "ai_request.canceled",
            item.request_id,
            {
                "reason": item.failure_reason,
                "last_worker_id": last_worker_id,
                "assignment_attempts": item.assignment_attempts,
                "no_per_task_payment": True,
            },
        )
        return item

    def get(self, request_id: str) -> AIInferenceRequest:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM ai_requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            raise KeyError(f"AI request not found: {request_id}")
        return AIInferenceRequest.model_validate(json.loads(row["payload"]))

    def list(self, limit: int = 100, requester_wallet: str | None = None) -> list[AIInferenceRequest]:
        safe_limit = max(1, min(int(limit), 1000))
        params: list[object] = []
        query = "SELECT payload FROM ai_requests"
        if requester_wallet:
            query += " WHERE requester_wallet = ?"
            params.append(requester_wallet.strip().upper())
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [AIInferenceRequest.model_validate(json.loads(row["payload"])) for row in rows]

    def put(self, item: AIInferenceRequest) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_requests (
                    request_id,
                    status,
                    assigned_worker_id,
                    requester_wallet,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    status = excluded.status,
                    assigned_worker_id = excluded.assigned_worker_id,
                    requester_wallet = excluded.requester_wallet,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    item.request_id,
                    item.status.value,
                    item.assigned_worker_id,
                    item.requester_wallet,
                    item.model_dump_json(),
                    item.updated_at.isoformat(),
                ),
            )

    def _release_expired_assignment(self, item: AIInferenceRequest, *, now) -> AIInferenceRequest:
        last_worker_id = item.assigned_worker_id
        if item.assignment_attempts >= ai_request_max_assignments():
            item.status = AIRequestStatus.FAILED
            item.failure_reason = "assignment_lease_expired_max_attempts"
            item.updated_at = now
            self.put(item)
            self.storage.record_event(
                "ai_request.failed",
                item.request_id,
                {
                    "reason": item.failure_reason,
                    "last_worker_id": last_worker_id,
                    "assignment_attempts": item.assignment_attempts,
                },
            )
            return item
        item.status = AIRequestStatus.QUEUED
        item.assigned_worker_id = None
        item.assigned_at = None
        item.lease_expires_at = None
        item.model_profile = None
        item.failure_reason = "assignment_lease_expired"
        item.updated_at = now
        self.put(item)
        self.storage.record_event(
            "ai_request.requeued",
            item.request_id,
            {
                "reason": item.failure_reason,
                "last_worker_id": last_worker_id,
                "assignment_attempts": item.assignment_attempts,
            },
        )
        return item


def worker_can_serve_ai(worker: WorkerState) -> bool:
    profile = worker.registration.ai_model_profile
    return bool(profile and profile.available and profile.model_name and profile.endpoint and worker.ai_model_score > 0)


def worker_matches_request(worker: WorkerState, item: AIInferenceRequest) -> bool:
    profile = worker.registration.ai_model_profile
    if not profile or not worker_can_serve_ai(worker):
        return False
    capabilities = {capability.lower() for capability in profile.capabilities}
    required = {capability.lower() for capability in item.required_capabilities}
    if required and not required.issubset(capabilities):
        return False
    if item.model_hint and item.model_hint.lower() not in (profile.model_name or "").lower():
        return False
    if item.preferred_provider and item.preferred_provider.lower() != profile.provider.lower():
        return False
    if profile.parameter_count_b < item.min_parameter_count_b:
        return False
    if profile.context_tokens < item.min_context_tokens:
        return False
    return True


def ai_route_candidate(worker: WorkerState, item: AIInferenceRequest, active_requests: int = 0) -> dict:
    profile = worker.registration.ai_model_profile
    if profile is None:
        raise ValueError("worker has no AI model profile")
    capability_bonus = len(set(capability.lower() for capability in profile.capabilities)) * 2.0
    context_bonus = min(float(profile.context_tokens) / 4096.0, 50.0)
    model_size_bonus = min(float(profile.parameter_count_b) * 2.0, 100.0)
    reliability_bonus = float(worker.reliability_score) * 0.5
    uptime_bonus = float(worker.uptime_score) * 0.25
    load_penalty = float(active_requests) * 50.0
    routing_score = round(
        float(worker.ai_model_score)
        + model_size_bonus
        + context_bonus
        + capability_bonus
        + reliability_bonus
        + uptime_bonus
        - load_penalty,
        8,
    )
    return {
        "worker_id": worker.registration.worker_id,
        "wallet": worker.registration.wallet,
        "model_name": profile.model_name,
        "provider": profile.provider,
        "parameter_count_b": profile.parameter_count_b,
        "context_tokens": profile.context_tokens,
        "capabilities": profile.capabilities,
        "ai_model_score": worker.ai_model_score,
        "uptime_score": worker.uptime_score,
        "reliability_score": worker.reliability_score,
        "active_requests": active_requests,
        "routing_score": routing_score,
        "matches_model_hint": bool(item.model_hint and item.model_hint.lower() in (profile.model_name or "").lower()),
    }


def ai_request_priority_key(item: AIInferenceRequest) -> tuple:
    return (-float(item.stake_snapshot_pi), item.created_at, item.request_id)


def ai_inference_receipt_payload(
    item: AIInferenceRequest,
    *,
    worker_id: str | None = None,
    output_hash: str | None = None,
) -> dict:
    return {
        "schema": "picoin-forge-ai-inference-receipt-v1",
        "request_id": item.request_id,
        "worker_id": worker_id or item.assigned_worker_id,
        "requester_wallet": item.requester_wallet,
        "prompt_hash": item.prompt_hash,
        "output_hash": output_hash or item.output_hash,
        "model_profile": item.model_profile.model_dump(mode="json") if item.model_profile else None,
        "no_l1_transaction_created": True,
        "no_per_task_payment": True,
    }


def ai_inference_receipt_hash(item: AIInferenceRequest, result: AIInferenceResult) -> str:
    return hash_json(
        ai_inference_receipt_payload(
            item,
            worker_id=result.worker_id,
            output_hash=sha256_text(result.output),
        )
    )


def ai_access_min_stake_pi() -> float:
    try:
        value = float(os.getenv("PICOIN_FORGE_AI_ACCESS_MIN_STAKE_PI", str(DEFAULT_AI_ACCESS_MIN_STAKE_PI)))
    except ValueError:
        return DEFAULT_AI_ACCESS_MIN_STAKE_PI
    return max(0.0, value)


def ai_request_lease_seconds() -> int:
    try:
        value = int(os.getenv("PICOIN_FORGE_AI_REQUEST_LEASE_SECONDS", str(DEFAULT_AI_REQUEST_LEASE_SECONDS)))
    except ValueError:
        return DEFAULT_AI_REQUEST_LEASE_SECONDS
    return max(1, value)


def ai_request_max_assignments() -> int:
    try:
        value = int(os.getenv("PICOIN_FORGE_AI_REQUEST_MAX_ASSIGNMENTS", str(DEFAULT_AI_REQUEST_MAX_ASSIGNMENTS)))
    except ValueError:
        return DEFAULT_AI_REQUEST_MAX_ASSIGNMENTS
    return max(1, value)
