import statistics

from app.core.difficulty import calculate_difficulty
from app.core.settings import RETARGET_MAX_PI_POSITION
from app.services.difficulty_service import DifficultyService


def test_get_position_bucket():
    assert DifficultyService.get_position_bucket(1) == "0-100"
    assert DifficultyService.get_position_bucket(10) == "0-100"
    assert DifficultyService.get_position_bucket(100) == "100-250"
    assert DifficultyService.get_position_bucket(249) == "100-250"
    assert DifficultyService.get_position_bucket(250) == "250-500"
    assert DifficultyService.get_position_bucket(499) == "250-500"
    assert DifficultyService.get_position_bucket(500) == "500-1000"
    assert DifficultyService.get_position_bucket(999) == "500-1000"
    assert DifficultyService.get_position_bucket(1000) == "1000-2500"
    assert DifficultyService.get_position_bucket(100000) == "100000-250000"
    assert DifficultyService.get_position_bucket(10**9) == "1000000000-2500000000"
    assert DifficultyService.get_position_bucket(0) == "0-100"
    assert DifficultyService.get_position_bucket(-50) == "0-100"


def test_handle_cold_start_no_history():
    current_params = {"segment_size": 64, "sample_count": 8, "max_pi_position": 10000}
    new_params, meta = DifficultyService._handle_cold_start([], current_params, 1000)
    assert new_params == current_params
    assert meta["action"] == "wait"
    assert meta["reason"] == "No history available"


def test_handle_cold_start_deeper_bucket_scaling():
    current_params = {"segment_size": 64, "sample_count": 8, "max_pi_position": 10000}
    history = [{"total_task_ms": 60000, "range_start": 1000, "range_end": 1063, "total_block_ms": 60000}]
    
    # Move from 1000 to 1,000,000 (much deeper)
    new_params, meta = DifficultyService._handle_cold_start(history, current_params, 1_000_000)
    
    assert new_params["segment_size"] < current_params["segment_size"]
    assert new_params["segment_size"] >= DifficultyService.MIN_SEGMENT_SIZE
    assert meta["action"] == "adjust_cold"
    assert "BBP Complexity scaling" in meta["reason"]
    assert meta["complexity_factor"] > 1.05


def test_handle_cold_start_shallower_bucket_no_scaling():
    current_params = {"segment_size": 64, "sample_count": 8, "max_pi_position": 10000}
    history = [{"total_task_ms": 60000, "range_start": 100000, "range_end": 100063, "total_block_ms": 60000}]
    
    # Move from 100000 to 1000 (shallower)
    new_params, meta = DifficultyService._handle_cold_start(history, current_params, 1000)
    
    assert new_params["segment_size"] == current_params["segment_size"]
    assert meta["action"] == "adjust_cold"
    assert "no scaling needed" in meta["reason"]
    assert meta["complexity_factor"] < 1.05


def test_calculate_next_difficulty_network_too_fast():
    current_params = {"segment_size": 64, "sample_count": 8, "max_pi_position": 10000}
    history = [
        {"total_task_ms": 30000, "range_start": 1000, "range_end": 1063, "total_block_ms": 35000}
        for _ in range(DifficultyService.RETARGET_WINDOW)
    ]
    
    new_params, meta = DifficultyService.calculate_next_difficulty(history, current_params, 1000)
    
    assert new_params["segment_size"] > current_params["segment_size"]
    assert new_params["segment_size"] <= current_params["segment_size"] + DifficultyService.MAX_SEGMENT_STEP
    assert meta["action"] == "increase"
    assert meta["adjustment_ratio"] < 1.0


def test_calculate_next_difficulty_network_too_slow():
    current_params = {"segment_size": 64, "sample_count": 8, "max_pi_position": 10000}
    history = [
        {"total_task_ms": 80000, "range_start": 1000, "range_end": 1063, "total_block_ms": 90000}
        for _ in range(DifficultyService.RETARGET_WINDOW)
    ]
    
    new_params, meta = DifficultyService.calculate_next_difficulty(history, current_params, 1000)
    
    assert new_params["segment_size"] < current_params["segment_size"]
    assert new_params["segment_size"] >= current_params["segment_size"] - DifficultyService.MAX_SEGMENT_STEP
    assert meta["action"] == "decrease"
    assert meta["adjustment_ratio"] > 1.0


def test_calculate_next_difficulty_within_deadband():
    current_params = {"segment_size": 64, "sample_count": 8, "max_pi_position": 10000}
    expected_params = dict(current_params, RETARGET_MAX_PI_POSITION=RETARGET_MAX_PI_POSITION)
    history = [
        {"total_task_ms": 55000, "range_start": 1000, "range_end": 1063, "total_block_ms": 60000}
        for _ in range(DifficultyService.RETARGET_WINDOW)
    ]
    
    new_params, meta = DifficultyService.calculate_next_difficulty(history, current_params, 1000)
    
    assert new_params == expected_params
    assert meta["action"] == "keep"
    assert meta["within_hysteresis"] is True
    assert "Within hysteresis" in meta["reason"]


def test_calculate_next_difficulty_hysteresis_keeps_72_seconds():
    current_params = {"segment_size": 64, "sample_count": 8, "max_pi_position": 10000}
    expected_params = dict(current_params, RETARGET_MAX_PI_POSITION=RETARGET_MAX_PI_POSITION)
    history = [
        {"total_task_ms": 72000, "range_start": 1000, "range_end": 1063, "total_block_ms": 72000}
        for _ in range(DifficultyService.RETARGET_WINDOW)
    ]

    new_params, meta = DifficultyService.calculate_next_difficulty(history, current_params, 1000)

    assert new_params == expected_params
    assert meta["action"] == "keep"
    assert meta["within_hysteresis"] is True


def test_calculate_next_difficulty_emergency_decrease():
    current_params = {"segment_size": 64, "sample_count": 8, "max_pi_position": 10000}
    history = [
        {"total_task_ms": DifficultyService.TARGET_BLOCK_MS * DifficultyService.EMERGENCY_THRESHOLD * 1.1, "range_start": 1000, "range_end": 1063, "total_block_ms": DifficultyService.TARGET_BLOCK_MS * DifficultyService.EMERGENCY_THRESHOLD * 1.1}
        for _ in range(DifficultyService.RETARGET_WINDOW)
    ]
    
    new_params, meta = DifficultyService.calculate_next_difficulty(history, current_params, 1000)
    
    assert new_params["segment_size"] < current_params["segment_size"]
    assert meta["action"] == "decrease"
    assert "Emergency" in meta["reason"]
    assert meta["adjustment_ratio"] == 2.0


def test_calculate_next_difficulty_segment_and_sample_limits():
    # Test segment_size hits MAX, then sample_count increases
    current_params = {"segment_size": DifficultyService.MAX_SEGMENT_SIZE, "sample_count": 8, "max_pi_position": 10000}
    history_fast = [
        {"total_task_ms": 10000, "range_start": 1000, "range_end": 1000 + DifficultyService.MAX_SEGMENT_SIZE - 1, "total_block_ms": 15000}
        for _ in range(DifficultyService.RETARGET_WINDOW)
    ]
    new_params_fast, _ = DifficultyService.calculate_next_difficulty(history_fast, current_params, 1000)
    assert new_params_fast["segment_size"] == DifficultyService.MAX_SEGMENT_SIZE
    assert new_params_fast["sample_count"] > current_params["sample_count"]
    assert new_params_fast["sample_count"] <= DifficultyService.MAX_SAMPLE_COUNT

    # Test segment_size hits MIN, then sample_count decreases
    current_params = {"segment_size": DifficultyService.MIN_SEGMENT_SIZE, "sample_count": 128, "max_pi_position": 10000}
    history_slow = [
        {"total_task_ms": 100000, "range_start": 1000, "range_end": 1000 + DifficultyService.MIN_SEGMENT_SIZE - 1, "total_block_ms": 110000}
        for _ in range(DifficultyService.RETARGET_WINDOW)
    ]
    new_params_slow, _ = DifficultyService.calculate_next_difficulty(history_slow, current_params, 1000)
    assert new_params_slow["segment_size"] == DifficultyService.MIN_SEGMENT_SIZE
    assert new_params_slow["sample_count"] < current_params["sample_count"]
    assert new_params_slow["sample_count"] >= DifficultyService.MIN_SAMPLE_COUNT


def test_calculate_next_difficulty_bucket_filtering():
    current_params = {"segment_size": 64, "sample_count": 8, "max_pi_position": 10000}
    history = [
        {"total_task_ms": 30000, "range_start": 1000, "range_end": 1063, "total_block_ms": 35000},
        {"total_task_ms": 90000, "range_start": 100000, "range_end": 100063, "total_block_ms": 95000},
        {"total_task_ms": 32000, "range_start": 1000, "range_end": 1063, "total_block_ms": 37000},
        {"total_task_ms": 85000, "range_start": 100000, "range_end": 100063, "total_block_ms": 90000},
    ] * 5

    new_params, meta = DifficultyService.calculate_next_difficulty(history, current_params, 1500)
    
    expected_median_miner_ms = statistics.median([30000, 32000] * 5)
    assert meta["observed_median_ms"] == int(expected_median_miner_ms)
    assert meta["bucket"] == "1000-2500"
    assert new_params["segment_size"] > current_params["segment_size"]
    assert new_params["segment_size"] <= current_params["segment_size"] + DifficultyService.MAX_SEGMENT_STEP


def test_bbp_guardrail_is_inactive_below_one_million_positions():
    current_params = {"difficulty": 0.125, "segment_size": 32, "sample_count": 32, "max_pi_position": 10000}
    history = [
        {
            "total_task_ms": 60000,
            "range_start": 500000,
            "range_end": 500031,
            "segment_size": 32,
            "sample_count": 32,
            "difficulty": 0.125,
            "validation_ms": 1000,
            "total_block_ms": 60000,
        }
        for _ in range(DifficultyService.RETARGET_WINDOW)
    ]

    new_params, meta = DifficultyService.calculate_next_difficulty(history, current_params, 900000)

    assert meta["within_hysteresis"] is True
    assert meta["bbp_guardrail_active"] is False
    assert new_params["segment_size"] == current_params["segment_size"]
    assert new_params["difficulty"] == current_params["difficulty"]


def test_bbp_guardrail_above_one_million_reduces_segment_by_max_two():
    current_params = {
        "difficulty": 0.125,
        "segment_size": 32,
        "sample_count": 32,
        "max_pi_position": 10000,
        "RETARGET_MAX_PI_POSITION": 2_000_000,
    }
    history = [
        {
            "total_task_ms": 60000,
            "range_start": 500000,
            "range_end": 500031,
            "segment_size": 32,
            "sample_count": 32,
            "difficulty": 0.125,
            "validation_ms": 1000,
            "total_block_ms": 60000,
        }
        for _ in range(DifficultyService.RETARGET_WINDOW)
    ]

    new_params, meta = DifficultyService.calculate_next_difficulty(history, current_params, 2_000_000)

    assert meta["within_hysteresis"] is True
    assert meta["bbp_guardrail_active"] is True
    assert meta["predicted_next_task_ms"] > DifficultyService.DEADBAND_HIGH
    assert new_params["difficulty"] == current_params["difficulty"]
    assert new_params["segment_size"] == current_params["segment_size"] - DifficultyService.MAX_SEGMENT_STEP


def test_calculate_difficulty_visual_metric():
    params = {"segment_size": 128, "sample_count": 16, "max_pi_position": 100000}
    difficulty = calculate_difficulty(params)
    assert difficulty == 5.0

    params_min = {"segment_size": 8, "sample_count": 8, "max_pi_position": 100}
    difficulty_min = calculate_difficulty(params_min)
    assert difficulty_min == 0.0625

    params_max = {"segment_size": 1024, "sample_count": 128, "max_pi_position": 10**9}
    difficulty_max = calculate_difficulty(params_max)
    assert difficulty_max == 384.0
