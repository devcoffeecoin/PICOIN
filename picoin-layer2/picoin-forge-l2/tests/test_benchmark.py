from __future__ import annotations

from picoin_forge_l2.worker.benchmark import run_benchmark


def test_benchmark_generates_scores_and_hash():
    result = run_benchmark("worker_demo", scale=1)

    assert result.worker_id == "worker_demo"
    assert result.cpu_score > 0
    assert result.ram_score > 0
    assert result.io_score > 0
    assert result.gpu_score == 0
    assert result.benchmark_score > 0
    assert len(result.result_hash) == 64
