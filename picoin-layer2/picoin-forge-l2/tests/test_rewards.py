from __future__ import annotations

import json

from picoin_forge_l2.coordinator.epoch_engine import EpochEngine
from picoin_forge_l2.coordinator.worker_registry import WorkerRegistry
from picoin_forge_l2.worker.benchmark import run_benchmark
from picoin_forge_l2.worker.registration import register_worker


def test_epoch_close_writes_proportional_settlement(tmp_path):
    registry = WorkerRegistry(tmp_path)

    first = register_worker("PIWORKERONE", tmp_path / "w1")
    second = register_worker("PIWORKERTWO", tmp_path / "w2")
    registry.register(first)
    registry.register(second)

    b1 = run_benchmark(first.worker_id, scale=1)
    b2 = run_benchmark(second.worker_id, scale=1)
    b2.cpu_score = b1.cpu_score * 2
    b2.benchmark_score = b2.cpu_score + b2.ram_score + b2.io_score + b2.gpu_score
    registry.update_benchmark(b1)
    registry.update_benchmark(b2)

    settlement = EpochEngine(tmp_path, registry).close_epoch(epoch_reward=100.0)
    settlement_path = tmp_path / "settlement_epoch_1.json"
    saved = json.loads(settlement_path.read_text(encoding="utf-8"))

    assert settlement.epoch_id == 1
    assert settlement_path.exists()
    assert settlement.total_verified_compute > 0
    assert round(sum(worker.reward_pi for worker in settlement.workers), 8) == 100.0
    assert saved["result_hash"] == settlement.result_hash
