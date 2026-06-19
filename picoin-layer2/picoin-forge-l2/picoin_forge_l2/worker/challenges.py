from __future__ import annotations

import tempfile
import time
from pathlib import Path

from picoin_forge_l2.common.hashing import sha256_bytes, sha256_text
from picoin_forge_l2.common.models import ChallengeResult, ChallengeType, ComputeChallenge
from picoin_forge_l2.worker.ai_model import ai_model_expected_prompt_hash, run_ai_model_availability_challenge
from picoin_forge_l2.worker.gpu import gpu_expected_workload_hash, run_gpu_workload_challenge


def solve_challenge(challenge: ComputeChallenge) -> ChallengeResult:
    started = time.perf_counter()
    if challenge.challenge_type == ChallengeType.AI_MODEL:
        proof = run_ai_model_availability_challenge(challenge.seed, challenge.difficulty)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return ChallengeResult(
            challenge_id=challenge.challenge_id,
            worker_id=challenge.worker_id,
            result_hash=proof.proof_hash,
            passed=proof.verified and proof.prompt_hash == challenge.expected_hash,
            elapsed_ms=elapsed_ms,
            proof={
                "ai_model_verified": proof.verified,
                "prompt_hash": proof.prompt_hash,
                "proof_hash": proof.proof_hash,
                "output_hash": proof.output_hash,
                "backend": proof.backend,
                "model_profile": proof.model_profile,
                "reason": proof.reason,
            },
        )
    if challenge.challenge_type == ChallengeType.GPU:
        proof = run_gpu_workload_challenge(challenge.seed, challenge.difficulty)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return ChallengeResult(
            challenge_id=challenge.challenge_id,
            worker_id=challenge.worker_id,
            result_hash=proof.result_hash,
            passed=proof.verified and proof.result_hash == challenge.expected_hash,
            elapsed_ms=elapsed_ms,
            proof={
                "gpu_verified": proof.verified,
                "backend": proof.backend,
                "device_name": proof.device_name,
                "reason": proof.reason,
            },
        )
    result_hash = compute_challenge_hash(challenge.challenge_type, challenge.seed, challenge.difficulty)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return ChallengeResult(
        challenge_id=challenge.challenge_id,
        worker_id=challenge.worker_id,
        result_hash=result_hash,
        passed=result_hash == challenge.expected_hash,
        elapsed_ms=elapsed_ms,
    )


def compute_challenge_hash(challenge_type: ChallengeType, seed: str, difficulty: int) -> str:
    if challenge_type == ChallengeType.CPU:
        return cpu_challenge_hash(seed, difficulty)
    if challenge_type == ChallengeType.RAM:
        return ram_challenge_hash(seed, difficulty)
    if challenge_type == ChallengeType.IO:
        return io_challenge_hash(seed, difficulty)
    if challenge_type == ChallengeType.GPU:
        return gpu_expected_workload_hash(seed, difficulty)
    if challenge_type == ChallengeType.AI_MODEL:
        return ai_model_expected_prompt_hash(seed, difficulty)
    raise ValueError(f"unsupported challenge type: {challenge_type}")


def cpu_challenge_hash(seed: str, difficulty: int) -> str:
    value = seed
    rounds = max(1, difficulty) * 200
    for _ in range(rounds):
        value = sha256_text(value)
    return value


def ram_challenge_hash(seed: str, difficulty: int) -> str:
    size = max(1, difficulty) * 4096
    data = bytearray(size)
    seed_hash = sha256_text(seed)
    for idx in range(size):
        data[idx] = ord(seed_hash[idx % len(seed_hash)]) ^ (idx % 251)
    return sha256_bytes(bytes(data))


def io_challenge_hash(seed: str, difficulty: int) -> str:
    payload = (seed * max(1, difficulty) * 64).encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="picoin-forge-l2-") as tmp:
        path = Path(tmp) / "challenge.bin"
        path.write_bytes(payload)
        return sha256_bytes(path.read_bytes())
