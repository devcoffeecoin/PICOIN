from __future__ import annotations

from datetime import timedelta

from picoin_forge_l2.common.models import ChallengeType
from picoin_forge_l2.common.models import utc_now
from picoin_forge_l2.coordinator.challenge_engine import ChallengeEngine
from picoin_forge_l2.coordinator.worker_registry import WorkerRegistry
from picoin_forge_l2.worker.challenges import solve_challenge
from picoin_forge_l2.worker.registration import register_worker


def test_challenge_round_trip_passes(tmp_path):
    registry = WorkerRegistry(tmp_path)
    registration = register_worker("PI123456789", tmp_path / "worker")
    registry.register(registration)
    engine = ChallengeEngine(tmp_path, registry)

    challenge = engine.create_challenge(registration.worker_id, ChallengeType.CPU, difficulty=1)
    result = solve_challenge(challenge)
    passed = engine.verify_result(result)
    state = registry.get(registration.worker_id)

    assert passed is True
    assert state.passed_challenges == 1
    assert state.failed_challenges == 0
    assert state.reliability_score > 50.0
    assert (tmp_path / "coordinator.sqlite3").exists()
    assert any(event.event_type == "challenge.passed" for event in engine.storage.list_events())


def test_open_challenge_lists_are_persisted_in_sqlite(tmp_path):
    registry = WorkerRegistry(tmp_path)
    registration = register_worker("PISQLITE123456", tmp_path / "worker")
    registry.register(registration)
    engine = ChallengeEngine(tmp_path, registry)

    challenge = engine.create_challenge(registration.worker_id, ChallengeType.IO, difficulty=1)
    reloaded_engine = ChallengeEngine(tmp_path, WorkerRegistry(tmp_path))

    open_challenges = reloaded_engine.list_open_for_worker(registration.worker_id)

    assert len(open_challenges) == 1
    assert open_challenges[0].challenge_id == challenge.challenge_id


def test_expired_challenge_penalizes_worker(tmp_path):
    registry = WorkerRegistry(tmp_path)
    registration = register_worker("PIEXPIRED123456", tmp_path / "worker")
    registry.register(registration)
    engine = ChallengeEngine(tmp_path, registry)
    challenge = engine.create_challenge(registration.worker_id, ChallengeType.CPU, difficulty=1)
    challenge.deadline = utc_now() - timedelta(seconds=1)
    engine.put(challenge)

    expired = engine.expire_open_challenges()
    state = registry.get(registration.worker_id)

    assert len(expired) == 1
    assert expired[0].challenge_id == challenge.challenge_id
    assert state.failed_challenges == 1
    assert state.penalty_score > 0
    metrics = engine.storage.list_challenge_metrics(worker_id=registration.worker_id)
    assert len(metrics) == 1
    assert metrics[0]["status"] == "expired"
    assert metrics[0]["penalty_delta"] > 0
    assert any(event.event_type == "challenge.expired" for event in engine.storage.list_events())


def test_gpu_placeholder_challenge_passes_without_reward_signal(tmp_path):
    registry = WorkerRegistry(tmp_path)
    registration = register_worker("PIGPUPLACEHOLDER", tmp_path / "worker")
    registry.register(registration)
    engine = ChallengeEngine(tmp_path, registry)

    challenge = engine.create_challenge(registration.worker_id, ChallengeType.GPU, difficulty=1)
    result = solve_challenge(challenge)
    passed = engine.verify_result(result)
    state = registry.get(registration.worker_id)
    events = engine.storage.list_events()

    assert passed is True
    assert state.passed_challenges == 1
    assert state.reliability_score == 50.0
    assert any(
        event.event_type == "challenge.passed"
        and event.payload.get("gpu_placeholder") is True
        and event.payload.get("reliability_delta") == 0.0
        for event in events
    )
