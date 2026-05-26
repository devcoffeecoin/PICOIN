from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


PICOIN_UNIT = 1_000_000
PICOIN_DECIMALS = 6
_UNIT_DECIMAL = Decimal(PICOIN_UNIT)
_AMOUNT_QUANT = Decimal("0.000001")


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value).strip())
    except (AttributeError, InvalidOperation) as exc:
        raise ValueError(f"invalid Picoin amount: {value!r}") from exc


def to_units(amount: Any) -> int:
    value = _decimal(amount).quantize(_AMOUNT_QUANT, rounding=ROUND_HALF_UP)
    return int((value * _UNIT_DECIMAL).to_integral_value(rounding=ROUND_HALF_UP))


def units_from_db(amount: Any, units: Any = None) -> int:
    if units is not None:
        try:
            parsed_units = int(units)
            amount_units = to_units(amount)
            if parsed_units != 0 and amount_units != parsed_units:
                raise ValueError(f"amount/unit mismatch: {amount!r} != {parsed_units}")
            if parsed_units != 0 or amount_units == 0:
                return parsed_units
        except (TypeError, ValueError):
            if int(units or 0) != 0:
                raise
    return to_units(amount)


def from_units(units: int) -> Decimal:
    return (Decimal(int(units)) / _UNIT_DECIMAL).quantize(_AMOUNT_QUANT)


def canonical_amount(units: int) -> str:
    return f"{from_units(units):.{PICOIN_DECIMALS}f}"


def units_to_float(units: int) -> float:
    return float(canonical_amount(units))
