from __future__ import annotations

from picoin_forge_l2.common.constants import (
    CPU_WEIGHT,
    GPU_WEIGHT,
    IO_WEIGHT,
    RAM_WEIGHT,
    RELIABILITY_WEIGHT,
    UPTIME_WEIGHT,
)
from picoin_forge_l2.common.models import ScoreWeights, WorkerState


DEFAULT_WEIGHTS = ScoreWeights(
    cpu_weight=CPU_WEIGHT,
    gpu_weight=GPU_WEIGHT,
    ram_weight=RAM_WEIGHT,
    io_weight=IO_WEIGHT,
    uptime_weight=UPTIME_WEIGHT,
    reliability_weight=RELIABILITY_WEIGHT,
)


def calculate_verified_compute_score(state: WorkerState, weights: ScoreWeights = DEFAULT_WEIGHTS) -> float:
    benchmark = state.benchmark
    if benchmark is None:
        return 0.0
    score = (
        benchmark.cpu_score * weights.cpu_weight
        + benchmark.gpu_score * weights.gpu_weight
        + benchmark.ram_score * weights.ram_weight
        + benchmark.io_score * weights.io_weight
        + state.uptime_score * weights.uptime_weight
        + state.reliability_score * weights.reliability_weight
        - state.penalty_score
    )
    return round(max(0.0, score), 8)


def refresh_worker_score(state: WorkerState, weights: ScoreWeights = DEFAULT_WEIGHTS) -> WorkerState:
    state.verified_compute_score = calculate_verified_compute_score(state, weights)
    return state
