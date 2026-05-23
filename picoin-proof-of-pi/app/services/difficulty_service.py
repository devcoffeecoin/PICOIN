import logging
import math
import statistics
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.core.settings import (
    RETARGET_MAX_ADJUSTMENT_FACTOR,
    RETARGET_MAX_DIFFICULTY,
    RETARGET_MAX_PI_POSITION,
    RETARGET_MAX_SAMPLE_COUNT,
    RETARGET_MAX_SEGMENT_SIZE,
    RETARGET_MIN_DIFFICULTY,
    RETARGET_MIN_SAMPLE_COUNT,
    RETARGET_MIN_SEGMENT_SIZE,
    RETARGET_TARGET_BLOCK_MS,
    RETARGET_WINDOW_BLOCKS,
)


logger = logging.getLogger(__name__)


class DifficultyService:
    """Adaptive Proof-of-Pi difficulty retargeting.

    The service groups Pi positions into logarithmic buckets, uses medians
    instead of averages, and scales conservatively when mining jumps into a
    deeper BBP range without local history.
    """

    TARGET_BLOCK_MS = RETARGET_TARGET_BLOCK_MS
    MINER_RATIO = 0.65
    VALIDATOR_RATIO = 0.25
    CONSENSUS_RATIO = 0.10
    TARGET_MINER_MS = TARGET_BLOCK_MS * MINER_RATIO

    RETARGET_WINDOW = RETARGET_WINDOW_BLOCKS
    RETARGET_INTERVAL = RETARGET_WINDOW_BLOCKS

    DEADBAND_LOW = 45_000
    DEADBAND_HIGH = 75_000

    MAX_SEGMENT_SIZE = RETARGET_MAX_SEGMENT_SIZE
    MIN_SEGMENT_SIZE = RETARGET_MIN_SEGMENT_SIZE
    MAX_SAMPLE_COUNT = RETARGET_MAX_SAMPLE_COUNT
    MIN_SAMPLE_COUNT = RETARGET_MIN_SAMPLE_COUNT

    MAX_DECREASE = 0.85
    MAX_INCREASE = 1.15
    MAX_SEGMENT_STEP = 2
    PI_PIVOT_POS = 10_000
    PI_GUARDRAIL_START = 1_000_000
    EMERGENCY_THRESHOLD = 3
    MIN_DIFFICULTY = RETARGET_MIN_DIFFICULTY
    MAX_DIFFICULTY = RETARGET_MAX_DIFFICULTY
    MAX_DIFFICULTY_ADJUSTMENT = min(RETARGET_MAX_ADJUSTMENT_FACTOR, MAX_INCREASE)

    @staticmethod
    def calculate_next_target_difficulty(
        *,
        old_difficulty: float,
        average_block_ms: float,
    ) -> tuple[float, dict[str, Any]]:
        params = {"difficulty": old_difficulty, "segment_size": 64, "sample_count": 8, "max_pi_position": 10_000}
        history = [
            {
                "range_start": 1,
                "range_end": 64,
                "segment_size": 64,
                "sample_count": 8,
                "difficulty": old_difficulty,
                "total_task_ms": average_block_ms,
                "validation_ms": 0,
                "total_block_ms": average_block_ms,
            }
            for _ in range(DifficultyService.RETARGET_WINDOW)
        ]
        next_params, meta = DifficultyService.calculate_next_difficulty(history, params, 1)
        return float(next_params["difficulty"]), meta

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

    @classmethod
    def _calculate_bbp_cost_factor(cls, position: int) -> float:
        """Relative BBP cost using n*ln(n), enabled as a guardrail for deep Pi ranges."""
        safe_position = max(10, int(position or 10))
        pivot = max(10, int(cls.PI_PIVOT_POS))
        return (safe_position * math.log(safe_position)) / (pivot * math.log(pivot))

    @staticmethod
    def calculate_next_difficulty(
        history: list[dict[str, Any]],
        current_params: dict[str, Any],
        next_range_start: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return DifficultyService.calculate_next_protocol_params(history, current_params, next_range_start)

    @staticmethod
    def calculate_next_protocol_params(
        history: list[dict[str, Any]],
        current_params: dict[str, Any],
        next_range_start: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        new_params = dict(current_params)
        try:
            RETARGET_MAX_PI_POSITION_value = int(
                current_params.get("RETARGET_MAX_PI_POSITION")
                or current_params.get("retarget_max_pi_position")
                or RETARGET_MAX_PI_POSITION
            )
        except (TypeError, ValueError):
            RETARGET_MAX_PI_POSITION_value = RETARGET_MAX_PI_POSITION
        RETARGET_MAX_PI_POSITION_value = max(1, RETARGET_MAX_PI_POSITION_value)
        new_params["retarget_max_pi_position"] = RETARGET_MAX_PI_POSITION_value
        capped_next_range_start = min(max(1, int(next_range_start or 1)), RETARGET_MAX_PI_POSITION_value)
        target_bucket = DifficultyService.get_position_bucket(capped_next_range_start)
        valid_history = [
            block
            for block in history[-DifficultyService.RETARGET_WINDOW :]
            if DifficultyService._decimal(block.get("total_block_ms") or 0) > 0
        ]

        if not valid_history:
            return new_params, {
                "action": "wait",
                "reason": "No valid timing history available",
                "bucket": target_bucket,
                "adjustment_factor": 1.0,
                "difficulty_factor": 1.0,
                "sample_factor": 1.0,
            }

        bucket_history = [
            block for block in valid_history if DifficultyService.get_position_bucket(int(block["range_start"])) == target_bucket
        ]
        if not bucket_history:
            new_params, cold_meta = DifficultyService._handle_cold_start(valid_history, new_params, capped_next_range_start)
            bucket_history = valid_history
        else:
            cold_meta = {}

        stats = DifficultyService._window_stats(bucket_history, target_bucket)
        target = Decimal(str(DifficultyService.TARGET_BLOCK_MS))
        raw_factor = target / max(Decimal("1"), stats["avg_total_block_ms"])
        total_factor = DifficultyService._clamp_adjustment(raw_factor)
        old_difficulty = DifficultyService._current_difficulty(current_params)
        current_segment = int(current_params.get("segment_size") or 64)
        cold_target_segment = int(new_params["segment_size"]) if new_params.get("segment_size") != current_params.get("segment_size") else None
        current_samples = int(new_params.get("sample_count") or current_params.get("sample_count") or 8)
        difficulty_factor = Decimal("1")
        sample_factor = Decimal("1")
        reasons: list[str] = []

        within_deadband = (
            Decimal(str(DifficultyService.DEADBAND_LOW))
            <= stats["avg_total_block_ms"]
            <= Decimal(str(DifficultyService.DEADBAND_HIGH))
        )
        too_slow = stats["avg_total_block_ms"] > target
        too_fast = stats["avg_total_block_ms"] < target
        emergency = stats["avg_task_ms"] > target * Decimal(str(DifficultyService.EMERGENCY_THRESHOLD))
        mining_dominates = stats["mining_ratio"] > Decimal("0.60")
        validation_dominates = stats["validation_ratio"] > Decimal("0.40")
        validation_cheap = stats["validation_ratio"] < Decimal("0.20")

        if within_deadband:
            reasons.append("Within hysteresis band")
        elif too_slow and mining_dominates:
            difficulty_factor = total_factor
            reasons.append("mining bottleneck")
        elif too_slow and not validation_dominates:
            difficulty_factor = total_factor
            reasons.append("total block time high")
        elif too_fast:
            difficulty_factor = total_factor
            reasons.append("blocks below target")

        if within_deadband:
            sample_factor = Decimal("1")
        elif too_slow and validation_dominates:
            sample_factor = total_factor
            reasons.append("validation bottleneck")
        elif too_fast and validation_cheap:
            sample_factor = total_factor
            reasons.append("validation cheap")
        if cold_meta.get("reason"):
            reasons.append(str(cold_meta["reason"]))
        if emergency:
            reasons.append("Emergency")

        new_difficulty = DifficultyService._clamp_difficulty(old_difficulty * difficulty_factor)
        target_segment = int(
            (Decimal(current_segment) * (new_difficulty / max(old_difficulty, Decimal("0.000001")))).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
        if cold_target_segment is not None:
            target_segment = min(target_segment, cold_target_segment)
        guardrail_meta = DifficultyService._bbp_position_guardrail(
            bucket_history,
            current_segment,
            target_segment,
            capped_next_range_start,
        )
        if guardrail_meta["active"]:
            reasons.append("BBP position guardrail")
        target_segment = int(guardrail_meta["target_segment"])
        new_segment = DifficultyService._clamp_int(
            DifficultyService._limit_segment_step(current_segment, target_segment),
            DifficultyService.MIN_SEGMENT_SIZE,
            DifficultyService.MAX_SEGMENT_SIZE,
        )
        new_samples = DifficultyService._clamp_int(
            int((Decimal(current_samples) * sample_factor).to_integral_value(rounding=ROUND_HALF_UP)),
            DifficultyService.MIN_SAMPLE_COUNT,
            DifficultyService.MAX_SAMPLE_COUNT,
        )
        new_params["difficulty"] = DifficultyService._quantize(new_difficulty)
        new_params["segment_size"] = new_segment
        new_params["sample_count"] = new_samples

        action = "keep"
        if Decimal(str(new_params["difficulty"])) > old_difficulty or new_segment > current_segment or new_samples > current_samples:
            action = "increase"
        if Decimal(str(new_params["difficulty"])) < old_difficulty or new_segment < current_segment or new_samples < current_samples:
            action = "decrease" if action == "keep" else "mixed"

        if action == "keep":
            reasons.append("Within deadband / target envelope")
            if current_params.get("difficulty") is None:
                new_params.pop("difficulty", None)

        legacy_adjustment_ratio = stats["avg_task_ms"] / max(Decimal("1"), Decimal(str(DifficultyService.TARGET_MINER_MS)))
        legacy_adjustment_ratio = max(Decimal("0.000001"), legacy_adjustment_ratio)
        if emergency:
            legacy_adjustment_ratio = Decimal("2.0")
        new_difficulty_value = DifficultyService._quantize(new_difficulty)
        cold_details = {
            key: value
            for key, value in cold_meta.items()
            if key not in {"action", "reason", "adjustment_factor", "adjustment_ratio"}
        }

        return new_params, {
            "action": action,
            "reason": "; ".join(reasons),
            "bucket": target_bucket,
            "bucket_history_used": len(bucket_history),
            "source_window": len(valid_history),
            "avg_total_block_ms": DifficultyService._float(stats["avg_total_block_ms"]),
            "avg_task_ms": DifficultyService._float(stats["avg_task_ms"]),
            "avg_validation_ms": DifficultyService._float(stats["avg_validation_ms"]),
            "avg_segment_size": DifficultyService._float(stats["avg_segment_size"]),
            "avg_sample_count": DifficultyService._float(stats["avg_sample_count"]),
            "avg_difficulty": DifficultyService._float(stats["avg_difficulty"]),
            "mining_ratio": DifficultyService._float(stats["mining_ratio"]),
            "validation_ratio": DifficultyService._float(stats["validation_ratio"]),
            "raw_adjustment_factor": DifficultyService._float(raw_factor),
            "adjustment_factor": DifficultyService._float(total_factor),
            "adjustment_ratio": DifficultyService._float(legacy_adjustment_ratio),
            "difficulty_factor": DifficultyService._float(difficulty_factor),
            "sample_factor": DifficultyService._float(sample_factor),
            "within_hysteresis": within_deadband,
            "bbp_guardrail_active": guardrail_meta["active"],
            "bbp_guardrail_start": DifficultyService.PI_GUARDRAIL_START,
            "next_position_cost_factor": guardrail_meta["next_position_cost_factor"],
            "predicted_next_task_ms": guardrail_meta["predicted_next_task_ms"],
            "desired_segment_size": guardrail_meta["desired_segment_size"],
            "old_difficulty": DifficultyService._float(old_difficulty),
            "new_difficulty": new_difficulty_value,
            "old_segment_size": current_segment,
            "new_segment_size": new_segment,
            "old_sample_count": current_samples,
            "new_sample_count": new_samples,
            "observed_median_ms": int(statistics.median(float(block.get("total_task_ms") or 0) for block in bucket_history)),
            **cold_details,
        }

    @staticmethod
    def bucket_metrics(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets: dict[str, list[dict[str, Any]]] = {}
        for block in history:
            if DifficultyService._decimal(block.get("total_block_ms") or 0) <= 0:
                continue
            bucket_id = DifficultyService.get_position_bucket(int(block["range_start"]))
            buckets.setdefault(bucket_id, []).append(block)
        metrics: list[dict[str, Any]] = []
        for bucket_id in sorted(buckets):
            stats = DifficultyService._window_stats(buckets[bucket_id], bucket_id)
            start, end = DifficultyService._bucket_bounds(bucket_id)
            metrics.append(
                {
                    "bucket_id": bucket_id,
                    "range_start_min": start,
                    "range_start_max": end,
                    "avg_task_ms": DifficultyService._float(stats["avg_task_ms"]),
                    "avg_validation_ms": DifficultyService._float(stats["avg_validation_ms"]),
                    "avg_total_block_ms": DifficultyService._float(stats["avg_total_block_ms"]),
                    "avg_segment_size": DifficultyService._float(stats["avg_segment_size"]),
                    "avg_sample_count": DifficultyService._float(stats["avg_sample_count"]),
                    "avg_difficulty": DifficultyService._float(stats["avg_difficulty"]),
                    "samples_seen": len(buckets[bucket_id]),
                }
            )
        return metrics

    @staticmethod
    def _window_stats(history: list[dict[str, Any]], bucket_id: str) -> dict[str, Decimal]:
        total_values = [DifficultyService._decimal(block.get("total_block_ms") or 0) for block in history]
        task_values = [DifficultyService._decimal(block.get("total_task_ms") or block.get("total_block_ms") or 0) for block in history]
        validation_values = [DifficultyService._decimal(block.get("validation_ms") or 0) for block in history]
        segment_values = [
            DifficultyService._decimal(block.get("segment_size") or (int(block["range_end"]) - int(block["range_start"]) + 1))
            for block in history
        ]
        sample_values = [DifficultyService._decimal(block.get("sample_count") or 8) for block in history]
        difficulty_values = [DifficultyService._decimal(block.get("difficulty") or 0) for block in history]
        avg_total = DifficultyService._avg(total_values)
        avg_task = DifficultyService._avg(task_values)
        avg_validation = DifficultyService._avg(validation_values)
        return {
            "bucket_id": Decimal(0),
            "avg_total_block_ms": avg_total,
            "avg_task_ms": avg_task,
            "avg_validation_ms": avg_validation,
            "avg_segment_size": DifficultyService._avg(segment_values),
            "avg_sample_count": DifficultyService._avg(sample_values),
            "avg_difficulty": DifficultyService._avg(difficulty_values),
            "mining_ratio": avg_task / max(Decimal("1"), avg_total),
            "validation_ratio": avg_validation / max(Decimal("1"), avg_total),
        }

    @staticmethod
    def _avg(values: list[Decimal]) -> Decimal:
        if not values:
            return Decimal("0")
        return sum(values, Decimal("0")) / Decimal(len(values))

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        return Decimal(str(value or 0))

    @staticmethod
    def _float(value: Decimal) -> float:
        return float(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _quantize(value: Decimal) -> float:
        return float(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _clamp_adjustment(value: Decimal) -> Decimal:
        max_factor = Decimal(str(DifficultyService.MAX_DIFFICULTY_ADJUSTMENT))
        min_factor = Decimal(str(DifficultyService.MAX_DECREASE))
        return max(min_factor, min(max_factor, value))

    @staticmethod
    def _clamp_difficulty(value: Decimal) -> Decimal:
        return max(Decimal(str(DifficultyService.MIN_DIFFICULTY)), min(Decimal(str(DifficultyService.MAX_DIFFICULTY)), value))

    @staticmethod
    def _clamp_int(value: int, minimum: int, maximum: int) -> int:
        return max(minimum, min(maximum, value))

    @staticmethod
    def _limit_segment_step(current: int, target: int) -> int:
        if target > current:
            return min(target, current + DifficultyService.MAX_SEGMENT_STEP)
        if target < current:
            return max(target, current - DifficultyService.MAX_SEGMENT_STEP)
        return current

    @staticmethod
    def _bbp_position_guardrail(
        history: list[dict[str, Any]],
        current_segment: int,
        proposed_segment: int,
        next_range_start: int,
    ) -> dict[str, Any]:
        inactive = {
            "active": False,
            "target_segment": proposed_segment,
            "desired_segment_size": None,
            "next_position_cost_factor": None,
            "predicted_next_task_ms": None,
        }
        if int(next_range_start or 0) <= DifficultyService.PI_GUARDRAIL_START:
            return inactive

        costs: list[float] = []
        for block in history[-DifficultyService.RETARGET_WINDOW :]:
            task_ms = float(block.get("total_task_ms") or block.get("total_block_ms") or 0)
            segment = int(block.get("segment_size") or (int(block["range_end"]) - int(block["range_start"]) + 1) or 1)
            if task_ms <= 0 or segment <= 0:
                continue
            factor = DifficultyService._calculate_bbp_cost_factor(int(block["range_start"]))
            costs.append(task_ms / max(1, segment) / max(0.000001, factor))

        if not costs:
            return inactive

        cost_per_segment_unit = statistics.median(costs)
        next_factor = DifficultyService._calculate_bbp_cost_factor(int(next_range_start))
        predicted_ms = cost_per_segment_unit * max(1, proposed_segment) * next_factor
        if predicted_ms <= DifficultyService.DEADBAND_HIGH:
            return {
                **inactive,
                "next_position_cost_factor": round(next_factor, 6),
                "predicted_next_task_ms": round(predicted_ms, 2),
            }

        desired_segment = int(
            max(
                DifficultyService.MIN_SEGMENT_SIZE,
                min(
                    DifficultyService.MAX_SEGMENT_SIZE,
                    round(DifficultyService.TARGET_MINER_MS / max(0.000001, cost_per_segment_unit * next_factor)),
                ),
            )
        )
        guarded_segment = min(proposed_segment, desired_segment, current_segment - 1)
        guarded_segment = max(DifficultyService.MIN_SEGMENT_SIZE, guarded_segment)
        return {
            "active": guarded_segment < proposed_segment,
            "target_segment": guarded_segment if guarded_segment < proposed_segment else proposed_segment,
            "desired_segment_size": desired_segment,
            "next_position_cost_factor": round(next_factor, 6),
            "predicted_next_task_ms": round(predicted_ms, 2),
        }

    @staticmethod
    def _current_difficulty(params: dict[str, Any]) -> Decimal:
        configured = params.get("difficulty")
        if configured is not None:
            return Decimal(str(configured))
        segment = Decimal(int(params.get("segment_size", 64) or 64)) / Decimal(64)
        samples = Decimal(int(params.get("sample_count", 8) or 8)) / Decimal(8)
        max_pos = max(100, int(params.get("max_pi_position", 10_000) or 10_000))

        if max_pos > 1_000_000:
            position = (Decimal("6") / Decimal("4")) * (Decimal(max_pos) / Decimal("1000000"))
        else:
            position = Decimal(str(math.log10(max_pos))) / Decimal("4")
            
        return (segment * samples * position).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _bucket_bounds(bucket_id: str) -> tuple[int, int]:
        start, end = bucket_id.split("-", 1)
        return int(start), int(end)

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
