import random
from dataclasses import dataclass
from typing import Any

from app.core.crypto import hash_result
from app.core.pi import calculate_pi_segment, is_valid_segment_charset, normalize_segment
from app.core.settings import SAMPLE_COUNT


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    reason: str
    expected_hash: str
    samples: list[dict[str, Any]]


def build_samples(segment: str, range_start: int, count: int = SAMPLE_COUNT) -> list[dict[str, Any]]:
    if not segment:
        return []

    sample_count = min(count, len(segment))
    seed = f"{range_start}:{len(segment)}:{segment[:4]}:{segment[-4:]}"
    randomizer = random.Random(seed)
    indexes = sorted(randomizer.sample(range(len(segment)), sample_count))

    return [
        {
            "position": range_start + index,
            "digit": segment[index],
        }
        for index in indexes
    ]


def validate_submission(
    *,
    range_start: int,
    range_end: int,
    algorithm: str,
    submitted_segment: str,
    submitted_hash: str,
) -> ValidationResult:
    submitted_segment = normalize_segment(submitted_segment, algorithm)

    if not is_valid_segment_charset(submitted_segment, algorithm):
        return ValidationResult(False, "segment contains symbols not allowed by algorithm", "", [])

    expected_length = range_end - range_start + 1
    if len(submitted_segment) != expected_length:
        return ValidationResult(False, "segment length does not match assigned range", "", [])

    try:
        expected_segment = calculate_pi_segment(range_start, range_end, algorithm)
    except ValueError as exc:
        return ValidationResult(False, str(exc), "", [])
    expected_hash = hash_result(expected_segment, range_start, range_end, algorithm)
    samples = build_samples(expected_segment, range_start)

    if submitted_hash != expected_hash:
        return ValidationResult(False, "result_hash does not match independently calculated hash", expected_hash, samples)

    for sample in samples:
        index = sample["position"] - range_start
        if submitted_segment[index] != sample["digit"]:
            return ValidationResult(False, "random sample validation failed", expected_hash, samples)

    if submitted_segment != expected_segment:
        return ValidationResult(False, "submitted segment does not match expected pi segment", expected_hash, samples)

    return ValidationResult(True, "accepted", expected_hash, samples)
