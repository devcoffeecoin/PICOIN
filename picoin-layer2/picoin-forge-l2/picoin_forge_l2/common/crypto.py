from __future__ import annotations

import secrets

from .hashing import sha256_text


def simulated_public_key(wallet: str, salt: str | None = None) -> str:
    seed = f"{wallet}:{salt or secrets.token_hex(16)}"
    return "sim-ed25519:" + sha256_text(seed)[:44]


def worker_id_from_wallet(wallet: str, public_key: str) -> str:
    return "worker_" + sha256_text(f"{wallet}:{public_key}")[:16]
