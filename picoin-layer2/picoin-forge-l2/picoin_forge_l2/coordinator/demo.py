from __future__ import annotations

from pathlib import Path

from picoin_forge_l2.common.models import ChallengeType, DemoResult
from picoin_forge_l2.worker.benchmark import run_benchmark
from picoin_forge_l2.worker.challenges import solve_challenge
from picoin_forge_l2.worker.heartbeat import build_heartbeat
from picoin_forge_l2.worker.registration import register_worker

from .challenge_engine import ChallengeEngine
from .epoch_engine import EpochEngine
from .worker_registry import WorkerRegistry


def run_demo_network(
    state_dir: str | Path,
    *,
    worker_count: int = 3,
    epoch_reward: float = 100.0,
) -> DemoResult:
    registry = WorkerRegistry(state_dir)
    challenge_engine = ChallengeEngine(state_dir, registry)
    passed = 0
    for index in range(max(1, worker_count)):
        wallet = f"PIDEMOWORKER{index:04d}"
        registration = register_worker(wallet, Path(state_dir) / "demo-workers" / f"worker-{index}")
        registry.register(registration)
        registry.update_benchmark(run_benchmark(registration.worker_id, scale=1))
        registry.heartbeat(build_heartbeat(registration.worker_id))
        for challenge_type in (ChallengeType.CPU, ChallengeType.RAM, ChallengeType.IO):
            challenge = challenge_engine.create_challenge(registration.worker_id, challenge_type, difficulty=1)
            result = solve_challenge(challenge)
            if challenge_engine.verify_result(result):
                passed += 1
    settlement = EpochEngine(state_dir, registry).close_epoch(epoch_reward=epoch_reward)
    return DemoResult(workers_created=max(1, worker_count), challenges_passed=passed, settlement=settlement)
