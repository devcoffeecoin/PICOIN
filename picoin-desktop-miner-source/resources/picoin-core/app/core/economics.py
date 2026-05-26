from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.core.money import to_units, units_to_float
from app.core.settings import (
    PROOF_OF_PI_REWARD_PERCENT,
    SCIENCE_COMPUTE_REWARD_PERCENT_OF_BLOCK,
    SCIENTIFIC_DEVELOPMENT_REWARD_PERCENT_OF_BLOCK,
    VALIDATOR_REWARD_PERCENT_OF_BLOCK,
)


BASIS_POINTS = 10_000


def _basis_points(percent: Any) -> int:
    return int((Decimal(str(percent)) * BASIS_POINTS).to_integral_value(rounding=ROUND_HALF_UP))


def _percent_units(total_units: int, percent: Any) -> int:
    bps = _basis_points(percent)
    return int((Decimal(int(total_units)) * bps / BASIS_POINTS).to_integral_value(rounding=ROUND_HALF_UP))


PROOF_OF_PI_REWARD_BPS = _basis_points(PROOF_OF_PI_REWARD_PERCENT)
VALIDATOR_REWARD_BPS = _basis_points(VALIDATOR_REWARD_PERCENT_OF_BLOCK)
SCIENCE_COMPUTE_REWARD_BPS = _basis_points(SCIENCE_COMPUTE_REWARD_PERCENT_OF_BLOCK)
SCIENTIFIC_DEVELOPMENT_REWARD_BPS = _basis_points(SCIENTIFIC_DEVELOPMENT_REWARD_PERCENT_OF_BLOCK)


def total_block_reward_units(params: dict[str, Any]) -> int:
    return to_units(params["base_reward"])


def miner_reward_units(params: dict[str, Any]) -> int:
    return _percent_units(total_block_reward_units(params), PROOF_OF_PI_REWARD_PERCENT)


def validator_reward_pool_units(params: dict[str, Any]) -> int:
    return _percent_units(total_block_reward_units(params), VALIDATOR_REWARD_PERCENT_OF_BLOCK)


def science_reserve_units_from_total(total_reward_units: int) -> int:
    return _percent_units(total_reward_units, SCIENCE_COMPUTE_REWARD_PERCENT_OF_BLOCK)


def scientific_development_units_from_total(total_reward_units: int) -> int:
    return _percent_units(total_reward_units, SCIENTIFIC_DEVELOPMENT_REWARD_PERCENT_OF_BLOCK)


def total_units_from_miner_reward_units(miner_units: int) -> int:
    return int(
        (Decimal(int(miner_units)) * BASIS_POINTS / PROOF_OF_PI_REWARD_BPS).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )


def reward_units_to_float(units: int) -> float:
    return units_to_float(int(units))
