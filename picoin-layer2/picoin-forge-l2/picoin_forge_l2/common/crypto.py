from __future__ import annotations

import base64
import secrets

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from .hashing import sha256_bytes
from .hashing import sha256_text


def simulated_public_key(wallet: str, salt: str | None = None) -> str:
    seed = f"{wallet}:{salt or secrets.token_hex(16)}"
    return "sim-ed25519:" + sha256_text(seed)[:44]


def worker_id_from_wallet(wallet: str, public_key: str) -> str:
    return "worker_" + sha256_text(f"{wallet}:{public_key}")[:16]


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def generate_ed25519_private_key() -> str:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    return "ed25519-private:" + b64url_encode(raw)


def public_key_from_private_key(private_key: str) -> str:
    raw = _strip_prefixed_key(private_key, "ed25519-private:")
    key = Ed25519PrivateKey.from_private_bytes(raw)
    public_raw = key.public_key().public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return "ed25519:" + b64url_encode(public_raw)


def sign_message(private_key: str, message: bytes) -> str:
    raw = _strip_prefixed_key(private_key, "ed25519-private:")
    signature = Ed25519PrivateKey.from_private_bytes(raw).sign(message)
    return "ed25519:" + b64url_encode(signature)


def verify_message(public_key: str, message: bytes, signature: str) -> bool:
    try:
        public_raw = _strip_prefixed_key(public_key, "ed25519:")
        signature_raw = _strip_prefixed_key(signature, "ed25519:")
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature_raw, message)
        return True
    except (InvalidSignature, ValueError):
        return False


def request_signing_payload(method: str, path: str, timestamp: str, body: bytes) -> bytes:
    body_hash = sha256_bytes(body)
    return "\n".join([method.upper(), path, timestamp, body_hash]).encode("utf-8")


def _strip_prefixed_key(value: str, prefix: str) -> bytes:
    if not value.startswith(prefix):
        raise ValueError(f"expected key prefix {prefix}")
    return b64url_decode(value[len(prefix) :])
