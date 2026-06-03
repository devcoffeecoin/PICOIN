from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.core.crypto import canonical_json, sha256_text
from app.core.money import canonical_amount, to_units, units_to_float
from app.core.settings import CHAIN_ID, NETWORK_ID
from app.core.signatures import generate_keypair, sign_payload


ADDRESS_PREFIX = "PI"
ADDRESS_HASH_LENGTH = 38
ADDRESS_CHECKSUM_LENGTH = 8
LEGACY_ADDRESS_PATTERN = re.compile(rf"^{ADDRESS_PREFIX}[0-9A-F]{{{ADDRESS_HASH_LENGTH}}}$")
ADDRESS_PATTERN = re.compile(rf"^{ADDRESS_PREFIX}[0-9A-F]{{{ADDRESS_HASH_LENGTH + ADDRESS_CHECKSUM_LENGTH}}}$")


def address_checksum(address_body: str) -> str:
    return sha256_text(address_body).upper()[:ADDRESS_CHECKSUM_LENGTH]


def legacy_address_from_public_key(public_key: str) -> str:
    body = sha256_text(public_key).upper()[:ADDRESS_HASH_LENGTH]
    return f"{ADDRESS_PREFIX}{body}"


def address_from_public_key(public_key: str) -> str:
    body = legacy_address_from_public_key(public_key)[len(ADDRESS_PREFIX) :]
    return f"{ADDRESS_PREFIX}{body}{address_checksum(body)}"


def address_matches_public_key(address: str | None, public_key: str | None) -> bool:
    if not isinstance(public_key, str):
        return False
    if not is_valid_address(address):
        return False
    normalized = str(address).strip().upper()
    return normalized in {address_from_public_key(public_key), legacy_address_from_public_key(public_key)}


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


def create_wallet(
    name: str = "picoin-wallet",
    *,
    network_id: str | None = None,
    chain_id: str | int | None = None,
) -> dict[str, Any]:
    keypair = generate_keypair()
    address = address_from_public_key(keypair["public_key"])
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "version": 1,
        "name": name,
        "address": address,
        "public_key": keypair["public_key"],
        "private_key": keypair["private_key"],
        "network_id": network_id or NETWORK_ID,
        "chain_id": _normalize_chain_id(chain_id or CHAIN_ID),
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
    chain_id: str | int = CHAIN_ID,
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


def _normalize_chain_id(value: str | int) -> str | int:
    if isinstance(value, int):
        return value
    cleaned = str(value).strip()
    return int(cleaned) if cleaned.isdigit() else cleaned


def transaction_hash(unsigned_payload: dict[str, Any], public_key: str) -> str:
    return sha256_text(canonical_json({"public_key": public_key, "tx": unsigned_payload}))


def transaction_signature_payload_candidates(unsigned_payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(payload: dict[str, Any]) -> None:
        marker = canonical_json(payload)
        if marker in seen:
            return
        seen.add(marker)
        candidates.append(payload)

    def timestamp_variants(value: Any) -> list[Any]:
        if not isinstance(value, str):
            return [value]
        variants = [value]
        if value.endswith("Z"):
            variants.append(f"{value[:-1]}+00:00")
        elif value.endswith("+00:00"):
            variants.append(f"{value[:-6]}Z")
        for suffix in ("Z", "+00:00"):
            if value.endswith(suffix) and "." in value.removesuffix(suffix):
                prefix = value.removesuffix(suffix)
                whole, fraction = prefix.rsplit(".", 1)
                trimmed = fraction.rstrip("0")
                if trimmed:
                    compact = f"{whole}.{trimmed}{suffix}"
                    variants.append(compact)
                    variants.append(f"{compact[:-1]}+00:00" if compact.endswith("Z") else f"{compact[:-6]}Z")
        return variants

    def chain_id_variants(value: Any) -> list[Any]:
        variants = [value]
        if isinstance(value, int):
            variants.append(str(value))
        elif isinstance(value, str) and value.isdigit():
            variants.append(int(value))
        return variants

    def money_variants(value: Any, units_key: str) -> list[Any]:
        variants = [value]
        units = unsigned_payload.get(units_key)
        if units is not None:
            amount_float = units_to_float(int(units))
            variants.append(amount_float)
            variants.append(canonical_amount(int(units)))
        return variants

    def unit_modes(payload: dict[str, Any]) -> list[dict[str, Any]]:
        variants = [dict(payload)]
        if "amount_units" in payload or "fee_units" in payload:
            legacy = dict(payload)
            legacy.pop("amount_units", None)
            legacy.pop("fee_units", None)
            variants.append(legacy)
        return variants

    def recipient_modes(payload: dict[str, Any]) -> list[dict[str, Any]]:
        variants = [dict(payload)]
        if payload.get("recipient") is None and "recipient" in payload:
            legacy = dict(payload)
            legacy.pop("recipient", None)
            variants.append(legacy)
        return variants

    base = dict(unsigned_payload)
    for timestamp in timestamp_variants(base.get("timestamp")):
        for chain_id in chain_id_variants(base.get("chain_id")):
            for amount in money_variants(base.get("amount"), "amount_units"):
                for fee in money_variants(base.get("fee"), "fee_units"):
                    current = {
                        **base,
                        "amount": amount,
                        "chain_id": chain_id,
                        "fee": fee,
                        "timestamp": timestamp,
                    }
                    for unit_payload in unit_modes(current):
                        for recipient_payload in recipient_modes(unit_payload):
                            add(recipient_payload)
    return candidates


def matching_transaction_signature_payload(
    unsigned_payload: dict[str, Any],
    public_key: str,
    tx_hash: str,
) -> dict[str, Any] | None:
    for candidate in transaction_signature_payload_candidates(unsigned_payload):
        if transaction_hash(candidate, public_key) == tx_hash:
            return candidate
    return None


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
