from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.core.crypto import canonical_json, sha256_text
from app.core.money import canonical_amount, to_units
from app.core.settings import CHAIN_ID, NETWORK_ID
from app.core.signatures import generate_keypair, sign_payload


ADDRESS_PREFIX = "PI"
ADDRESS_HASH_LENGTH = 38
ADDRESS_CHECKSUM_LENGTH = 8
LEGACY_ADDRESS_PATTERN = re.compile(rf"^{ADDRESS_PREFIX}[0-9A-F]{{{ADDRESS_HASH_LENGTH}}}$")
ADDRESS_PATTERN = re.compile(rf"^{ADDRESS_PREFIX}[0-9A-F]{{{ADDRESS_HASH_LENGTH + ADDRESS_CHECKSUM_LENGTH}}}$")


def address_checksum(address_body: str) -> str:
    return sha256_text(address_body).upper()[:ADDRESS_CHECKSUM_LENGTH]


def address_from_public_key(public_key: str) -> str:
    body = sha256_text(public_key).upper()[:ADDRESS_HASH_LENGTH]
    return f"{ADDRESS_PREFIX}{body}{address_checksum(body)}"


def is_valid_address(address: str | None) -> bool:
    if not isinstance(address, str):
        return False
    normalized = address.strip().upper()
    if LEGACY_ADDRESS_PATTERN.fullmatch(normalized):
        return True
    if not ADDRESS_PATTERN.fullmatch(normalized):
        return False
    body = normalized[len(ADDRESS_PREFIX) : len(ADDRESS_PREFIX) + ADDRESS_HASH_LENGTH]
    checksum = normalized[-ADDRESS_CHECKSUM_LENGTH:]
    return checksum == address_checksum(body)


def create_wallet(name: str = "picoin-wallet") -> dict[str, Any]:
    keypair = generate_keypair()
    address = address_from_public_key(keypair["public_key"])
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "version": 1,
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
    amount_units = to_units(amount)
    fee_units = to_units(fee)
    return {
        "amount": canonical_amount(amount_units),
        "amount_units": amount_units,
        "chain_id": chain_id,
        "fee": canonical_amount(fee_units),
        "fee_units": fee_units,
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
