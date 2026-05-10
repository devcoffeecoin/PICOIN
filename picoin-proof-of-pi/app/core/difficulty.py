import math
from typing import Any

from app.core.settings import (
    BASE_DIFFICULTY_MAX_PI_POSITION,
    BASE_DIFFICULTY_SAMPLE_COUNT,
    BASE_DIFFICULTY_SEGMENT_SIZE,
)


def calculate_difficulty(params: dict[str, Any]) -> float:
    segment_factor = params["segment_size"] / BASE_DIFFICULTY_SEGMENT_SIZE
    sample_factor = params["sample_count"] / BASE_DIFFICULTY_SAMPLE_COUNT
    position_factor = math.log10(max(10, params["max_pi_position"])) / math.log10(BASE_DIFFICULTY_MAX_PI_POSITION)
    return round(max(0.01, segment_factor * sample_factor * position_factor), 6)


def calculate_reward(params: dict[str, Any]) -> float:
    return round(float(params["base_reward"]) * calculate_difficulty(params), 8)
