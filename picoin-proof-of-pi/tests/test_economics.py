from app.core.economics import (
    miner_reward_units,
    reward_units_to_float,
    science_reserve_units_from_total,
    scientific_development_units_from_total,
    total_block_reward_units,
    total_units_from_miner_reward_units,
    validator_reward_pool_units,
)


def test_block_reward_split_uses_integer_units() -> None:
    params = {"base_reward": 3.1416}
    total_units = total_block_reward_units(params)
    miner_units = miner_reward_units(params)
    reserve_units = science_reserve_units_from_total(total_units)
    validator_units = validator_reward_pool_units(params)
    treasury_units = scientific_development_units_from_total(total_units)

    assert total_units == 3_141_600
    assert miner_units == 2_513_280
    assert reserve_units == 219_912
    assert validator_units == 314_160
    assert treasury_units == 94_248
    assert miner_units + reserve_units + validator_units + treasury_units == total_units


def test_total_reward_can_be_reconstructed_from_miner_reward_units() -> None:
    params = {"base_reward": 3.1416}
    total_units = total_block_reward_units(params)

    assert total_units_from_miner_reward_units(miner_reward_units(params)) == total_units


def test_reward_units_to_float_is_only_display_conversion() -> None:
    assert reward_units_to_float(219_912) == 0.219912
    assert reward_units_to_float(94_248) == 0.094248
