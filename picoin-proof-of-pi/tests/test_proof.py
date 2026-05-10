from app.core.crypto import hash_result
from app.core.pi import calculate_pi_hex_digits
from validator.proof import validate_submission


def test_calculate_first_pi_hex_digits() -> None:
    assert calculate_pi_hex_digits(1, 16) == "243F6A8885A308D3"


def test_validator_accepts_correct_segment() -> None:
    segment = calculate_pi_hex_digits(1, 25)
    result_hash = hash_result(segment, 1, 25, "bbp_hex_v1")

    result = validate_submission(
        range_start=1,
        range_end=25,
        algorithm="bbp_hex_v1",
        submitted_segment=segment,
        submitted_hash=result_hash,
    )

    assert result.accepted is True
    assert result.samples


def test_validator_rejects_bad_hash() -> None:
    segment = calculate_pi_hex_digits(1, 25)

    result = validate_submission(
        range_start=1,
        range_end=25,
        algorithm="bbp_hex_v1",
        submitted_segment=segment,
        submitted_hash="0" * 64,
    )

    assert result.accepted is False
