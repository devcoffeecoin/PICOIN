import math
from typing import Any

from app.core.settings import (
    BASE_DIFFICULTY_MAX_PI_POSITION,
    BASE_DIFFICULTY_SAMPLE_COUNT,
    BASE_DIFFICULTY_SEGMENT_SIZE,
    RETARGET_MAX_ADJUSTMENT_FACTOR,
    RETARGET_MAX_PI_POSITION,
    RETARGET_MAX_SAMPLE_COUNT,
    RETARGET_MAX_SEGMENT_SIZE,
    RETARGET_MIN_PI_POSITION,
    RETARGET_MIN_SAMPLE_COUNT,
    RETARGET_MIN_SEGMENT_SIZE,
    RETARGET_TARGET_BLOCK_MS,
    RETARGET_TOLERANCE,
)


def calculate_difficulty(params: dict[str, Any]) -> float:
    segment_factor = params["segment_size"] / BASE_DIFFICULTY_SEGMENT_SIZE
    sample_factor = params["sample_count"] / BASE_DIFFICULTY_SAMPLE_COUNT
    position_factor = math.log10(max(10, params["max_pi_position"])) / math.log10(BASE_DIFFICULTY_MAX_PI_POSITION)
    return round(max(0.01, segment_factor * sample_factor * position_factor), 6)


def calculate_reward(params: dict[str, Any]) -> float:
    return round(float(params["base_reward"]) * calculate_difficulty(params), 8)


def propose_retarget_params(
    params: dict[str, Any],
    average_block_ms: float,
    target_block_ms: int = RETARGET_TARGET_BLOCK_MS,
    tolerance: float = RETARGET_TOLERANCE,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ratio = target_block_ms / max(1.0, average_block_ms)
    lower = 1.0 - tolerance
    upper = 1.0 + tolerance

    if lower <= ratio <= upper:
        return dict(params), {
            "action": "keep",
            "reason": "average block time is inside tolerance",
            "adjustment_factor": 1.0,
        }

    adjustment_factor = min(RETARGET_MAX_ADJUSTMENT_FACTOR, max(1 / RETARGET_MAX_ADJUSTMENT_FACTOR, ratio))
    next_params = dict(params)
    old_difficulty = calculate_difficulty(params)
    direction = "increase" if adjustment_factor > 1 else "decrease"

    next_params["segment_size"] = _bounded_multiple(
        next_params["segment_size"] * adjustment_factor,
        minimum=RETARGET_MIN_SEGMENT_SIZE,
        maximum=RETARGET_MAX_SEGMENT_SIZE,
        multiple=8,
    )

    if next_params["segment_size"] == params["segment_size"]:
        sample_delta = 1 if direction == "increase" else -1
        next_params["sample_count"] = _bounded_int(
            next_params["sample_count"] + sample_delta,
            RETARGET_MIN_SAMPLE_COUNT,
            RETARGET_MAX_SAMPLE_COUNT,
        )

    if calculate_difficulty(next_params) == old_difficulty:
        next_params["max_pi_position"] = _bounded_multiple(
            next_params["max_pi_position"] * adjustment_factor,
            minimum=RETARGET_MIN_PI_POSITION,
            maximum=RETARGET_MAX_PI_POSITION,
            multiple=1000,
        )

    new_difficulty = calculate_difficulty(next_params)
    return next_params, {
        "action": direction if new_difficulty != old_difficulty else "keep",
        "reason": "average block time is outside tolerance",
        "adjustment_factor": round(adjustment_factor, 6),
        "old_difficulty": old_difficulty,
        "new_difficulty": new_difficulty,
    }


def _bounded_int(value: float, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(round(value))))


def _bounded_multiple(value: float, minimum: int, maximum: int, multiple: int) -> int:
    rounded = int(round(value / multiple) * multiple)
    return max(minimum, min(maximum, rounded))
