from __future__ import annotations

from datetime import timedelta

from picoin_forge_l2.common.models import WorkerStatus, utc_now
from picoin_forge_l2.common.models import WorkerState
from picoin_forge_l2.coordinator.score_engine import calculate_verified_compute_score, refresh_worker_score
from picoin_forge_l2.coordinator.worker_registry import WorkerRegistry
from picoin_forge_l2.worker.benchmark import run_benchmark
from picoin_forge_l2.worker.heartbeat import build_heartbeat
from picoin_forge_l2.worker.registration import register_worker


def test_score_uses_benchmark_uptime_reliability_and_penalty(tmp_path):
    registry = WorkerRegistry(tmp_path)
    registration = register_worker("PIABCDEF123", tmp_path / "worker")
    state: WorkerState = registry.register(registration)
    state.benchmark = run_benchmark(registration.worker_id, scale=1)
    state.uptime_score = 10
    state.reliability_score = 80
    state.penalty_score = 5

    score = calculate_verified_compute_score(state)
    refreshed = refresh_worker_score(state)

    assert score > 0
    assert refreshed.verified_compute_score == score


def test_score_includes_verified_ai_model_capacity(tmp_path):
    registry = WorkerRegistry(tmp_path)
    registration = register_worker("PIAISCORING123", tmp_path / "worker")
    state: WorkerState = registry.register(registration)
    state.benchmark = run_benchmark(registration.worker_id, scale=1)
    baseline = calculate_verified_compute_score(state)
    state.ai_model_score = 125.0

    score = calculate_verified_compute_score(state)

    assert score > baseline


def test_heartbeat_increases_uptime(tmp_path):
    registry = WorkerRegistry(tmp_path)
    registration = register_worker("PITESTHEARTBEAT", tmp_path / "worker")
    registry.register(registration)

    state = registry.heartbeat(build_heartbeat(registration.worker_id))

    assert state.uptime_score == 1.0
    assert state.last_heartbeat_at is not None


def test_missed_heartbeat_penalizes_worker(tmp_path):
    registry = WorkerRegistry(tmp_path)
    registration = register_worker("PIMISSEDHEARTBEAT", tmp_path / "worker")
    state = registry.register(registration)
    state.last_heartbeat_at = utc_now() - timedelta(seconds=600)
    state.uptime_score = 10.0
    state.reliability_score = 60.0
    registry.put(state)

    penalized = registry.penalize_missed_heartbeats(grace_seconds=60, penalty=3.0)
    updated = registry.get(registration.worker_id)

    assert len(penalized) == 1
    assert updated.registration.status == WorkerStatus.OFFLINE
    assert updated.penalty_score == 3.0
    assert updated.uptime_score == 7.0
