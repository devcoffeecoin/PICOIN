import base64
from typing import Any

from app.core.crypto import canonical_json

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
except ImportError:  # pragma: no cover - exercised only before dependencies are installed.
    InvalidSignature = None
    Ed25519PrivateKey = None
    Ed25519PublicKey = None
    Encoding = None
    NoEncryption = None
    PrivateFormat = None
    PublicFormat = None


KEY_PREFIX = "ed25519:"


def _require_crypto() -> None:
    if Ed25519PrivateKey is None or Ed25519PublicKey is None:
        raise RuntimeError("cryptography is required for Ed25519 signatures. Run: pip install -r requirements.txt")


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def encode_public_key(raw: bytes) -> str:
    return f"{KEY_PREFIX}{_b64encode(raw)}"


def encode_private_key(raw: bytes) -> str:
    return f"{KEY_PREFIX}{_b64encode(raw)}"


def decode_key(value: str) -> bytes:
    if not value.startswith(KEY_PREFIX):
        raise ValueError("key must use ed25519:<base64url> format")
    raw = _b64decode(value.removeprefix(KEY_PREFIX))
    if len(raw) != 32:
        raise ValueError("ed25519 key must be 32 raw bytes")
    return raw


def generate_keypair() -> dict[str, str]:
    _require_crypto()
    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    return {
        "private_key": encode_private_key(private_raw),
        "public_key": encode_public_key(public_raw),
    }


def validate_public_key(public_key: str) -> None:
    _require_crypto()
    Ed25519PublicKey.from_public_bytes(decode_key(public_key))


def canonical_signature_message(payload: dict[str, Any]) -> bytes:
    return canonical_json(payload).encode("utf-8")


def sign_payload(private_key_value: str, payload: dict[str, Any]) -> str:
    _require_crypto()
    private_key = Ed25519PrivateKey.from_private_bytes(decode_key(private_key_value))
    signature = private_key.sign(canonical_signature_message(payload))
    return _b64encode(signature)


def verify_payload_signature(public_key_value: str, payload: dict[str, Any], signature: str) -> bool:
    _require_crypto()
    public_key = Ed25519PublicKey.from_public_bytes(decode_key(public_key_value))
    try:
        public_key.verify(_b64decode(signature), canonical_signature_message(payload))
    except InvalidSignature:
        return False
    except ValueError:
        return False
    return True


def build_submission_signature_payload(
    *,
    task_id: str,
    miner_id: str,
    range_start: int,
    range_end: int,
    algorithm: str,
    result_hash: str,
    signed_at: str,
) -> dict[str, Any]:
    return {
        "algorithm": algorithm,
        "miner_id": miner_id,
        "range_end": range_end,
        "range_start": range_start,
        "result_hash": result_hash,
        "signed_at": signed_at,
        "task_id": task_id,
    }


def build_commit_signature_payload(
    *,
    task_id: str,
    miner_id: str,
    range_start: int,
    range_end: int,
    algorithm: str,
    result_hash: str,
    merkle_root: str,
    signed_at: str,
) -> dict[str, Any]:
    return {
        "algorithm": algorithm,
        "merkle_root": merkle_root,
        "miner_id": miner_id,
        "range_end": range_end,
        "range_start": range_start,
        "result_hash": result_hash,
        "signed_at": signed_at,
        "task_id": task_id,
    }


def build_reveal_signature_payload(
    *,
    task_id: str,
    miner_id: str,
    merkle_root: str,
    challenge_seed: str,
    signed_at: str,
) -> dict[str, Any]:
    return {
        "challenge_seed": challenge_seed,
        "merkle_root": merkle_root,
        "miner_id": miner_id,
        "signed_at": signed_at,
        "task_id": task_id,
    }


def build_validation_result_signature_payload(
    *,
    job_id: str,
    validator_id: str,
    task_id: str,
    approved: bool,
    reason: str,
    signed_at: str,
) -> dict[str, Any]:
    return {
        "approved": approved,
        "job_id": job_id,
        "reason": reason,
        "signed_at": signed_at,
        "task_id": task_id,
        "validator_id": validator_id,
    }
