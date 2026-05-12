from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.crypto import canonical_json, sha256_text
from app.core.settings import CHAIN_ID, NETWORK_ID
from app.core.signatures import generate_keypair, sign_payload


ADDRESS_PREFIX = "PI"
ADDRESS_HASH_LENGTH = 38


def address_from_public_key(public_key: str) -> str:
    return f"{ADDRESS_PREFIX}{sha256_text(public_key).upper()[:ADDRESS_HASH_LENGTH]}"


def create_wallet(name: str = "picoin-wallet") -> dict[str, Any]:
    keypair = generate_keypair()
    address = address_from_public_key(keypair["public_key"])
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "name": name,
        "address": address,
        "public_key": keypair["public_key"],
        "private_key": keypair["private_key"],
        "network_id": NETWORK_ID,
        "chain_id": CHAIN_ID,
        "created_at": timestamp,
    }


def unsigned_transaction_payload(
    *,
    tx_type: str,
    sender: str,
    nonce: int,
    fee: float = 0.0,
    recipient: str | None = None,
    amount: float = 0.0,
    payload: dict[str, Any] | None = None,
    timestamp: str | None = None,
    network_id: str = NETWORK_ID,
    chain_id: str = CHAIN_ID,
) -> dict[str, Any]:
    return {
        "amount": round(float(amount), 8),
        "chain_id": chain_id,
        "fee": round(float(fee), 8),
        "network_id": network_id,
        "nonce": int(nonce),
        "payload": payload or {},
        "recipient": recipient,
        "sender": sender,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "tx_type": tx_type,
    }


def transaction_hash(unsigned_payload: dict[str, Any], public_key: str) -> str:
    return sha256_text(canonical_json({"public_key": public_key, "tx": unsigned_payload}))


def sign_transaction(
    *,
    private_key: str,
    public_key: str,
    tx_type: str,
    sender: str,
    nonce: int,
    fee: float = 0.0,
    recipient: str | None = None,
    amount: float = 0.0,
    payload: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    unsigned_payload = unsigned_transaction_payload(
        tx_type=tx_type,
        sender=sender,
        recipient=recipient,
        amount=amount,
        nonce=nonce,
        fee=fee,
        payload=payload,
        timestamp=timestamp,
    )
    signature = sign_payload(private_key, unsigned_payload)
    tx_hash = transaction_hash(unsigned_payload, public_key)
    return {
        **unsigned_payload,
        "public_key": public_key,
        "signature": signature,
        "tx_hash": tx_hash,
    }
