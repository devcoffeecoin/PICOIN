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
        passed = result.worker_id == challenge.worker_id and result.result_hash == challenge.expected_hash
        challenge.status = ChallengeStatus.PASSED if passed else ChallengeStatus.FAILED
        self.put(challenge)
        state = self.registry.get(result.worker_id)
        if passed:
            state.passed_challenges += 1
            reliability_delta = 0.0 if challenge.challenge_type == ChallengeType.GPU else 5.0
            state.reliability_score = min(100.0, state.reliability_score + reliability_delta)
            event_type = "challenge.passed"
        else:
            reliability_delta = -10.0
            state.failed_challenges += 1
            state.penalty_score += 10.0
            state.reliability_score = max(0.0, state.reliability_score - 10.0)
            event_type = "challenge.failed"
        self.registry.put(state)
        self.storage.record_event(
            event_type,
            result.worker_id,
            {
                "challenge_id": challenge.challenge_id,
                "challenge_type": challenge.challenge_type.value,
                "result_hash": result.result_hash,
                "elapsed_ms": result.elapsed_ms,
                "reliability_delta": reliability_delta,
                "gpu_placeholder": challenge.challenge_type == ChallengeType.GPU,
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
