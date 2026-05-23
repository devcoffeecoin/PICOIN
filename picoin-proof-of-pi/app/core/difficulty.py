import math
from typing import Any

from app.core.settings import RETARGET_MAX_PI_POSITION


def calculate_difficulty(params: dict[str, Any]) -> float:
    """Visual workload metric normalized to segment=64, samples=8, position=10000."""
    configured = params.get("difficulty")
    if configured is not None:
        return round(max(0.001, float(configured)), 6)

    segment_size = int(params.get("segment_size", 64) or 64)
    sample_count = int(params.get("sample_count", 8) or 8)
    max_pos = int(params.get("max_pi_position", 10_000) or 10_000)
    configured_RETARGET_MAX_PI_POSITION = None
    for key, value in params.items():
        if str(key).upper() == "RETARGET_MAX_PI_POSITION":
            configured_RETARGET_MAX_PI_POSITION = value
            break
    retarget_max = int(configured_RETARGET_MAX_PI_POSITION or RETARGET_MAX_PI_POSITION)
    max_pos = min(max_pos, max(1, retarget_max))

    segment_factor = segment_size / 64
    sample_factor = sample_count / 8
    if max_pos > 1_000_000:
        # Escalado más agresivo después de 1M para compensar el costo de BBP
        position_factor = (6 / 4.0) * (max_pos / 1_000_000)
    else:
        position_factor = math.log10(max(100, max_pos)) / 4.0
    return round(max(0.001, segment_factor * sample_factor * position_factor), 6)


def calculate_reward(params: dict[str, Any]) -> float:
    return round(float(params["base_reward"]), 8)
