from __future__ import annotations

from datetime import timedelta

from picoin_forge_l2.common.models import ChallengeType, utc_now
from picoin_forge_l2.coordinator.challenge_engine import ChallengeEngine
from picoin_forge_l2.coordinator.maintenance import expire_challenges_once
from picoin_forge_l2.coordinator.worker_registry import WorkerRegistry
from picoin_forge_l2.worker.registration import register_worker


def test_expire_challenges_once_reports_expired_ids(tmp_path):
    registry = WorkerRegistry(tmp_path)
    registration = register_worker("PIMAINTENANCE123", tmp_path / "worker")
    registry.register(registration)
    engine = ChallengeEngine(tmp_path, registry)
    challenge = engine.create_challenge(registration.worker_id, ChallengeType.CPU, difficulty=1)
    challenge.deadline = utc_now() - timedelta(seconds=1)
    engine.put(challenge)

    result = expire_challenges_once(tmp_path)

    assert result["expired"] == 1
    assert result["challenge_ids"] == [challenge.challenge_id]
