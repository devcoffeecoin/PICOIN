import math
from decimal import Decimal, getcontext
from functools import lru_cache


HEX_DIGITS = "0123456789ABCDEF"


def _arctan_inverse(x: int) -> Decimal:
    """Return arctan(1 / x) using the Gregory series with Decimal precision."""
    x_decimal = Decimal(x)
    x_power = x_decimal
    total = Decimal(0)
    sign = 1
    term_index = 1
    threshold = Decimal(10) ** -(getcontext().prec + 2)

    while True:
        term = Decimal(sign) / (Decimal(term_index) * x_power)
        if abs(term) < threshold:
            break
        total += term
        x_power *= x_decimal * x_decimal
        sign *= -1
        term_index += 2

    return total


def calculate_pi_decimal_digits(range_start: int, range_end: int) -> str:
    """Calculate decimal digits of pi from range_start to range_end, inclusive.

    Positions are 1-based after the decimal point. For example, range 1..5
    returns "14159". This MVP intentionally uses Machin's formula:

        pi = 16 * arctan(1/5) - 4 * arctan(1/239)

    It is simple and understandable for small ranges. Future versions can
    replace this module with a spigot, BBP, or distributed-friendly algorithm
    without changing the API or validator contract.
    """
    if range_start < 1:
        raise ValueError("range_start must be >= 1")
    if range_end < range_start:
        raise ValueError("range_end must be >= range_start")

    guard_digits = 20
    precision = range_end + guard_digits
    getcontext().prec = precision

    pi_value = (Decimal(16) * _arctan_inverse(5)) - (Decimal(4) * _arctan_inverse(239))
    digits = format(pi_value, "f").split(".", maxsplit=1)[1]

    if len(digits) < range_end:
        digits = digits.ljust(range_end, "0")

    return digits[range_start - 1 : range_end]


def _bbp_series(j: int, n: int) -> float:
    """Return the BBP partial series for one denominator class.

    n is zero-based: n=0 calculates data needed for the first hexadecimal
    digit after the point in pi, whose known value is "2".
    """
    total = 0.0

    for k in range(n + 1):
        denominator = 8 * k + j
        total = (total + pow(16, n - k, denominator) / denominator) % 1.0

    k = n + 1
    while True:
        denominator = 8 * k + j
        term = math.pow(16.0, n - k) / denominator
        if term < 1e-17:
            break
        total += term
        k += 1

    return total % 1.0


@lru_cache(maxsize=4096)
def _bbp_hex_digit(zero_based_position: int) -> str:
    value = (
        4.0 * _bbp_series(1, zero_based_position)
        - 2.0 * _bbp_series(4, zero_based_position)
        - _bbp_series(5, zero_based_position)
        - _bbp_series(6, zero_based_position)
    )
    value = value - math.floor(value)
    return HEX_DIGITS[min(15, int(16.0 * value))]


def calculate_pi_hex_digits(range_start: int, range_end: int) -> str:
    """Calculate hexadecimal digits of pi after the point using BBP.

    Positions are 1-based after the hexadecimal point. Pi in hexadecimal
    starts as:

        3.243F6A8885A308D313198A2E...

    BBP is useful for Picoin because it can calculate a digit at a remote
    position without calculating every previous digit first.
    """
    if range_start < 1:
        raise ValueError("range_start must be >= 1")
    if range_end < range_start:
        raise ValueError("range_end must be >= range_start")

    return "".join(_bbp_hex_digit(position - 1) for position in range(range_start, range_end + 1))


def calculate_pi_segment(range_start: int, range_end: int, algorithm: str) -> str:
    if algorithm == "bbp_hex_v1":
        return calculate_pi_hex_digits(range_start, range_end)
    if algorithm == "machin_decimal_v1":
        return calculate_pi_decimal_digits(range_start, range_end)
    raise ValueError(f"unsupported pi algorithm: {algorithm}")


def pi_cache_info() -> dict[str, int]:
    info = _bbp_hex_digit.cache_info()
    return {
        "bbp_digit_cache_hits": info.hits,
        "bbp_digit_cache_misses": info.misses,
        "bbp_digit_cache_maxsize": info.maxsize or 0,
        "bbp_digit_cache_currsize": info.currsize,
    }


def calculate_pi_digits(range_start: int, range_end: int) -> str:
    """Backward-compatible alias for decimal digits."""
    return calculate_pi_decimal_digits(range_start, range_end)


def normalize_segment(segment: str, algorithm: str) -> str:
    if algorithm == "bbp_hex_v1":
        return segment.upper()
    return segment


def is_valid_segment_charset(segment: str, algorithm: str) -> bool:
    if algorithm == "bbp_hex_v1":
        return all(character in HEX_DIGITS for character in segment.upper())
    if algorithm == "machin_decimal_v1":
        return segment.isdigit()
    return False
