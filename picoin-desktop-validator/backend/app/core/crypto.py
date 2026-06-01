import hashlib
import json
from typing import Any


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def hash_result(segment: str, range_start: int, range_end: int, algorithm: str) -> str:
    payload = {
        "algorithm": algorithm,
        "range_end": range_end,
        "range_start": range_start,
        "segment": segment,
    }
    return sha256_text(canonical_json(payload))


def hash_block(block_data: dict[str, Any]) -> str:
    return sha256_text(canonical_json(block_data))
