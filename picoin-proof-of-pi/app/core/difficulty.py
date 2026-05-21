import math
from typing import Any


def calculate_difficulty(params: dict[str, Any]) -> float:
    """Visual workload metric normalized to segment=64, samples=8, position=10000."""
    configured = params.get("difficulty")
    if configured is not None:
        return round(max(0.001, float(configured)), 6)

    segment_size = int(params.get("segment_size", 64) or 64)
    sample_count = int(params.get("sample_count", 8) or 8)
    max_pos = int(params.get("max_pi_position", 10_000) or 10_000)

    segment_factor = segment_size / 64
    sample_factor = sample_count / 8
    position_factor = math.log10(max(100, max_pos)) / 4.0
    return round(max(0.001, segment_factor * sample_factor * position_factor), 6)


def calculate_reward(params: dict[str, Any]) -> float:
    return round(float(params["base_reward"]), 8)
