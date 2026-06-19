from __future__ import annotations

import json
import secrets
from datetime import timedelta
from pathlib import Path

from picoin_forge_l2.common.constants import DEFAULT_CHALLENGE_TTL_SECONDS
from picoin_forge_l2.common.constants import EXPIRED_CHALLENGE_PENALTY
from picoin_forge_l2.common.hashing import sha256_text
from picoin_forge_l2.common.models import ChallengeResult, ChallengeStatus, ChallengeType, ComputeChallenge, utc_now
from picoin_forge_l2.worker.challenges import compute_challenge_hash

from .storage import CoordinatorStorage
from .worker_registry import WorkerRegistry


class ChallengeEngine:
    def __init__(self, state_dir: str | Path, registry: WorkerRegistry):
        self.state_dir = Path(state_dir)
        self.storage = CoordinatorStorage(self.state_dir)
        self.registry = registry

    def create_challenge(
        self,
        worker_id: str,
        challenge_type: ChallengeType = ChallengeType.CPU,
        difficulty: int = 1,
    ) -> ComputeChallenge:
        seed = secrets.token_hex(16)
        expected_hash = compute_challenge_hash(challenge_type, seed, difficulty)
        challenge = ComputeChallenge(
            challenge_id="challenge_" + sha256_text(f"{worker_id}:{seed}")[:16],
            worker_id=worker_id,
            challenge_type=challenge_type,
            seed=seed,
            difficulty=difficulty,
            expected_hash=expected_hash,
            deadline=utc_now() + timedelta(seconds=DEFAULT_CHALLENGE_TTL_SECONDS),
            status=ChallengeStatus.ASSIGNED,
        )
        self.put(challenge)
        self.storage.record_event(
            "challenge.assigned",
            challenge.worker_id,
            {
                "challenge_id": challenge.challenge_id,
                "challenge_type": challenge.challenge_type.value,
                "difficulty": challenge.difficulty,
            },
        )
        return challenge

    def verify_result(self, result: ChallengeResult) -> bool:
        challenge = self.get(result.challenge_id)
        if challenge.status in {ChallengeStatus.PASSED, ChallengeStatus.FAILED, ChallengeStatus.EXPIRED}:
            return challenge.status == ChallengeStatus.PASSED
        if result.submitted_at > challenge.deadline:
            self._expire_challenge(challenge, reason="submitted_after_deadline")
            return False
        gpu_proof_valid = True
        ai_model_proof_valid = True
        result_hash_valid = result.result_hash == challenge.expected_hash
        if challenge.challenge_type == ChallengeType.GPU:
            gpu_proof_valid = bool(result.proof.get("gpu_verified")) and result.proof.get("backend") in {
                "cupy",
                "test-gpu",
            }
        if challenge.challenge_type == ChallengeType.AI_MODEL:
            ai_model_proof_valid = validate_ai_model_proof(result, challenge)
            result_hash_valid = ai_model_proof_valid
        passed = (
            result.worker_id == challenge.worker_id
            and result_hash_valid
            and gpu_proof_valid
            and ai_model_proof_valid
        )
        challenge.status = ChallengeStatus.PASSED if passed else ChallengeStatus.FAILED
        self.put(challenge)
        state = self.registry.get(result.worker_id)
        if passed:
            state.passed_challenges += 1
            reliability_delta = 5.0
            penalty_delta = 0.0
            gpu_score = apply_verified_gpu_score(state, result, challenge) if challenge.challenge_type == ChallengeType.GPU else None
            ai_model_score = (
                apply_verified_ai_model_score(state, result, challenge)
                if challenge.challenge_type == ChallengeType.AI_MODEL
                else None
            )
            state.reliability_score = min(100.0, state.reliability_score + reliability_delta)
            event_type = "challenge.passed"
        else:
            reliability_delta = -10.0
            penalty_delta = 10.0
            gpu_score = None
            ai_model_score = None
            state.failed_challenges += 1
            state.penalty_score += penalty_delta
            state.reliability_score = max(0.0, state.reliability_score - 10.0)
            event_type = "challenge.failed"
        self.registry.put(state)
        self.storage.record_challenge_metric(
            challenge_id=challenge.challenge_id,
            worker_id=result.worker_id,
            challenge_type=challenge.challenge_type,
            status=challenge.status.value,
            difficulty=challenge.difficulty,
            passed=passed,
            elapsed_ms=result.elapsed_ms,
            reliability_delta=reliability_delta,
            penalty_delta=penalty_delta,
            result_hash=result.result_hash,
        )
        self.storage.record_event(
            event_type,
            result.worker_id,
            {
                "challenge_id": challenge.challenge_id,
                "challenge_type": challenge.challenge_type.value,
                "result_hash": result.result_hash,
                "elapsed_ms": result.elapsed_ms,
                "reliability_delta": reliability_delta,
                "penalty_delta": penalty_delta,
                "gpu_verified": bool(result.proof.get("gpu_verified")),
                "gpu_backend": result.proof.get("backend"),
                "gpu_score": gpu_score,
                "ai_model_verified": bool(result.proof.get("ai_model_verified")),
                "ai_model_backend": result.proof.get("backend") if challenge.challenge_type == ChallengeType.AI_MODEL else None,
                "ai_model_score": ai_model_score,
                "ai_model_name": (result.proof.get("model_profile") or {}).get("model_name")
                if isinstance(result.proof.get("model_profile"), dict)
                else None,
            },
        )
        return passed

    def expire_open_challenges(self) -> list[ComputeChallenge]:
        expired: list[ComputeChallenge] = []
        now = utc_now()
        for challenge in self.list_open():
            if now <= challenge.deadline:
                continue
            expired.append(self._expire_challenge(challenge, reason="deadline_missed"))
        return expired

    def get(self, challenge_id: str) -> ComputeChallenge:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM challenges WHERE challenge_id = ?", (challenge_id,)).fetchone()
        if row is None:
            raise KeyError(f"challenge not found: {challenge_id}")
        return ComputeChallenge.model_validate(json.loads(row["payload"]))

    def list_for_worker(self, worker_id: str) -> list[ComputeChallenge]:
        with self.storage.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM challenges WHERE worker_id = ? ORDER BY updated_at DESC",
                (worker_id,),
            ).fetchall()
        return [ComputeChallenge.model_validate(json.loads(row["payload"])) for row in rows]

    def list_open_for_worker(self, worker_id: str) -> list[ComputeChallenge]:
        return [
            challenge
            for challenge in self.list_for_worker(worker_id)
            if challenge.status in {ChallengeStatus.CREATED, ChallengeStatus.ASSIGNED}
        ]

    def list_open(self) -> list[ComputeChallenge]:
        with self.storage.connect() as connection:
            rows = connection.execute("SELECT payload FROM challenges ORDER BY updated_at DESC").fetchall()
        return [
            challenge
            for challenge in (ComputeChallenge.model_validate(json.loads(row["payload"])) for row in rows)
            if challenge.status in {ChallengeStatus.CREATED, ChallengeStatus.ASSIGNED}
        ]

    def put(self, challenge: ComputeChallenge) -> None:
        now = utc_now().isoformat()
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO challenges (challenge_id, worker_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(challenge_id) DO UPDATE SET
                    worker_id = excluded.worker_id,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    challenge.challenge_id,
                    challenge.worker_id,
                    challenge.model_dump_json(),
                    now,
                ),
            )

    def _expire_challenge(self, challenge: ComputeChallenge, *, reason: str) -> ComputeChallenge:
        challenge.status = ChallengeStatus.EXPIRED
        self.put(challenge)
        state = self.registry.get(challenge.worker_id)
        state.failed_challenges += 1
        state.penalty_score += EXPIRED_CHALLENGE_PENALTY
        state.reliability_score = max(0.0, state.reliability_score - EXPIRED_CHALLENGE_PENALTY)
        self.registry.put(state)
        self.storage.record_challenge_metric(
            challenge_id=challenge.challenge_id,
            worker_id=challenge.worker_id,
            challenge_type=challenge.challenge_type,
            status=challenge.status.value,
            difficulty=challenge.difficulty,
            passed=False,
            reliability_delta=-EXPIRED_CHALLENGE_PENALTY,
            penalty_delta=EXPIRED_CHALLENGE_PENALTY,
        )
        self.storage.record_event(
            "challenge.expired",
            challenge.worker_id,
            {
                "challenge_id": challenge.challenge_id,
                "reason": reason,
                "penalty": EXPIRED_CHALLENGE_PENALTY,
            },
        )
        return challenge


def apply_verified_gpu_score(state, result: ChallengeResult, challenge: ComputeChallenge) -> float | None:
    if state.benchmark is None:
        return None
    elapsed_ms = max(float(result.elapsed_ms or 0.0), 1.0)
    difficulty_score = min(100.0, float(challenge.difficulty) * 10.0)
    speed_score = min(50.0, 1000.0 / elapsed_ms)
    score = round(difficulty_score + speed_score, 8)
    state.benchmark.gpu_score = max(float(state.benchmark.gpu_score or 0.0), score)
    return state.benchmark.gpu_score


def validate_ai_model_proof(result: ChallengeResult, challenge: ComputeChallenge) -> bool:
    profile = result.proof.get("model_profile") or {}
    if not isinstance(profile, dict):
        return False
    parameter_count_b = _safe_float(profile.get("parameter_count_b"), 0.0)
    return (
        bool(result.proof.get("ai_model_verified"))
        and result.proof.get("prompt_hash") == challenge.expected_hash
        and result.proof.get("proof_hash") == result.result_hash
        and result.proof.get("backend") in {"test-ai-model", "ollama", "openai-compatible"}
        and bool(profile.get("model_name"))
        and parameter_count_b > 0.0
    )


def apply_verified_ai_model_score(state, result: ChallengeResult, challenge: ComputeChallenge) -> float:
    profile = result.proof.get("model_profile") or {}
    parameter_count_b = max(_safe_float(profile.get("parameter_count_b"), 0.0), 0.0)
    context_tokens = max(_safe_int(profile.get("context_tokens"), 0), 0)
    capabilities = profile.get("capabilities") or []
    capability_bonus = min(len(capabilities) * 5.0, 50.0) if isinstance(capabilities, list) else 0.0
    difficulty_score = min(100.0, float(challenge.difficulty) * 10.0)
    model_size_score = min(600.0, parameter_count_b * 18.0)
    context_score = min(200.0, context_tokens / 512.0)
    score = round(model_size_score + context_score + capability_bonus + difficulty_score, 8)
    state.ai_model_score = max(float(state.ai_model_score or 0.0), score)
    return state.ai_model_score


def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
