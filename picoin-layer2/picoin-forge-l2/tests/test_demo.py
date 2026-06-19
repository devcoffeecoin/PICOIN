from __future__ import annotations

from picoin_forge_l2.coordinator.demo import run_demo_network


def test_demo_network_closes_epoch(tmp_path):
    result = run_demo_network(tmp_path, worker_count=3, epoch_reward=100.0)

    assert result.workers_created == 3
    assert result.challenges_passed == 9
    assert result.settlement.epoch_id == 1
    assert len(result.settlement.workers) == 3
    assert round(sum(worker.reward_pi for worker in result.settlement.workers), 8) == 100.0
