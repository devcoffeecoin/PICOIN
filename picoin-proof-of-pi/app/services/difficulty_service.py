import logging
import math
import statistics
from typing import Any


logger = logging.getLogger(__name__)


class DifficultyService:
    """Adaptive Proof-of-Pi difficulty retargeting.

    The service groups Pi positions into logarithmic buckets, uses medians
    instead of averages, and scales conservatively when mining jumps into a
    deeper BBP range without local history.
    """

    TARGET_BLOCK_MS = 60_000
    MINER_RATIO = 0.65
    VALIDATOR_RATIO = 0.25
    CONSENSUS_RATIO = 0.10
    TARGET_MINER_MS = TARGET_BLOCK_MS * MINER_RATIO

    RETARGET_WINDOW = 20
    RETARGET_INTERVAL = 10

    DEADBAND_LOW = 50_000
    DEADBAND_HIGH = 70_000

    MAX_SEGMENT_SIZE = 1024
    MIN_SEGMENT_SIZE = 8
    MAX_SAMPLE_COUNT = 128
    MIN_SAMPLE_COUNT = 8

    MAX_DECREASE = 0.70
    MAX_INCREASE = 1.25
    EMERGENCY_THRESHOLD = 3

    @staticmethod
    def get_position_bucket(pos: int) -> str:
        if pos < 100:
            return "0-100"
        exponent = math.floor(math.log10(pos))
        base = 10**exponent
        if pos < 2.5 * base:
            return f"{base}-{int(2.5 * base)}"
        if pos < 5 * base:
            return f"{int(2.5 * base)}-{5 * base}"
        return f"{5 * base}-{10 * base}"

    @staticmethod
    def calculate_next_difficulty(
        history: list[dict[str, Any]],
        current_params: dict[str, Any],
        next_range_start: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        new_params = dict(current_params)
        target_bucket = DifficultyService.get_position_bucket(next_range_start)
        bucket_history = [
            block
            for block in history[-DifficultyService.RETARGET_WINDOW :]
            if DifficultyService.get_position_bucket(int(block["range_start"])) == target_bucket
        ]

        if not bucket_history:
            return DifficultyService._handle_cold_start(history, current_params, next_range_start)

        median_miner_ms = statistics.median(float(block.get("total_task_ms") or 0) for block in bucket_history)
        median_segment = statistics.median(
            float(int(block["range_end"]) - int(block["range_start"]) + 1) for block in bucket_history
        )
        median_total_block_ms = statistics.median(
            float(block.get("total_block_ms") or block.get("total_task_ms") or 0) for block in bucket_history
        )
        ms_per_digit = median_miner_ms / max(1.0, median_segment)

        if DifficultyService.DEADBAND_LOW <= median_total_block_ms <= DifficultyService.DEADBAND_HIGH:
            return new_params, {
                "action": "keep",
                "reason": f"Within deadband ({int(median_total_block_ms)}ms)",
                "bucket": target_bucket,
                "observed_median_ms": int(median_miner_ms),
                "adjustment_ratio": 1.0,
                "adjustment_factor": 1.0,
                "ms_per_digit": round(ms_per_digit, 2),
            }

        adjustment_ratio = median_miner_ms / DifficultyService.TARGET_MINER_MS
        adjustment_ratio = max(DifficultyService.MAX_DECREASE, min(DifficultyService.MAX_INCREASE, adjustment_ratio))

        is_emergency = median_miner_ms > DifficultyService.TARGET_BLOCK_MS * DifficultyService.EMERGENCY_THRESHOLD
        if is_emergency:
            adjustment_ratio = 2.0
            logger.warning("EMERGENCY_DECREASE triggered: observed %.2fms", median_miner_ms)

        current_segment = int(current_params["segment_size"])
        current_samples = int(current_params["sample_count"])
        proposed_segment = int(current_segment / adjustment_ratio)
        new_params["segment_size"] = max(
            DifficultyService.MIN_SEGMENT_SIZE,
            min(DifficultyService.MAX_SEGMENT_SIZE, proposed_segment),
        )

        if new_params["segment_size"] == DifficultyService.MAX_SEGMENT_SIZE and adjustment_ratio < 1.0:
            new_params["sample_count"] = min(DifficultyService.MAX_SAMPLE_COUNT, max(current_samples + 1, int(current_samples * 1.1)))
        elif new_params["segment_size"] == DifficultyService.MIN_SEGMENT_SIZE and adjustment_ratio > 1.0:
            new_params["sample_count"] = max(DifficultyService.MIN_SAMPLE_COUNT, min(current_samples - 1, int(current_samples * 0.9)))

        action = "increase" if adjustment_ratio < 0.95 else "decrease" if adjustment_ratio > 1.05 else "keep"
        return new_params, {
            "action": action,
            "reason": "Emergency" if is_emergency else "Standard retarget",
            "bucket": target_bucket,
            "observed_median_ms": int(median_miner_ms),
            "adjustment_ratio": round(adjustment_ratio, 4),
            "adjustment_factor": round(adjustment_ratio, 4),
            "ms_per_digit": round(ms_per_digit, 2),
        }

    @staticmethod
    def _handle_cold_start(
        history: list[dict[str, Any]],
        current_params: dict[str, Any],
        next_pos: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not history:
            return dict(current_params), {
                "action": "wait",
                "reason": "No history available",
                "adjustment_ratio": 1.0,
                "adjustment_factor": 1.0,
            }

        ref_block = history[-1]
        ref_pos = max(100, int(ref_block["range_start"]))
        cur_pos = max(100, int(next_pos))
        complexity_factor = math.log10(cur_pos) / math.log10(ref_pos)
        new_params = dict(current_params)

        if complexity_factor > 1.05:
            new_params["segment_size"] = max(
                DifficultyService.MIN_SEGMENT_SIZE,
                int(int(current_params["segment_size"]) / complexity_factor),
            )
            reason = f"BBP Complexity scaling (factor {round(complexity_factor, 2)})"
        else:
            reason = "Bucket cold start (no scaling needed)"

        return new_params, {
            "action": "adjust_cold",
            "reason": reason,
            "complexity_factor": round(complexity_factor, 4),
            "adjustment_ratio": round(complexity_factor, 4),
            "adjustment_factor": round(complexity_factor, 4),
        }
