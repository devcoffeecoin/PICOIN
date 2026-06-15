from pathlib import Path

from app.core.crypto import canonical_json
from app.core.signatures import verify_payload_signature
from app.services.wallet import (
    address_from_public_key,
    matching_transaction_signature_payload,
    transaction_hash,
)


PRIVATE_KEY = "ed25519:2FDzM6exHBnQQa9IQ-bBfBVr3IJqqb9ec7X7yuHaYqc"
PUBLIC_KEY = "ed25519:i81FaEhRw3IJpKTyYDfGAzPI0cY-44_MnUyAds5u55g"
SENDER = "PI2C9F1631B1EF38DE481B1CC6361657AFCBC205E5B88CA9"
RECIPIENT = "PIEB4C49F30119C7B90A0DE0E338B8D3D8BFB6482A670E7C"
UNSIGNED_TRANSFER = {
    "amount": "1.234567",
    "amount_units": 1234567,
    "chain_id": 314159,
    "fee": "0.001000",
    "fee_units": 1000,
    "network_id": "picoin-mainnet-v1",
    "nonce": 7,
    "payload": {},
    "recipient": RECIPIENT,
    "sender": SENDER,
    "timestamp": "2026-06-14T15:55:01+00:00",
    "tx_type": "transfer",
}
CANONICAL_UNSIGNED_TRANSFER = (
    '{"amount":"1.234567","amount_units":1234567,"chain_id":314159,'
    '"fee":"0.001000","fee_units":1000,"network_id":"picoin-mainnet-v1",'
    '"nonce":7,"payload":{},"recipient":"PIEB4C49F30119C7B90A0DE0E338B8D3D8BFB6482A670E7C",'
    '"sender":"PI2C9F1631B1EF38DE481B1CC6361657AFCBC205E5B88CA9",'
    '"timestamp":"2026-06-14T15:55:01+00:00","tx_type":"transfer"}'
)
TX_HASH = "af0276ebb7bf438dc3d03a66698c4f0a821397e07072dcd603947251bffd5937"
SIGNATURE = "drXzCc_ehuquSkTlvwud0LNoRK-8TmWD9iUPIh7Tn2g6AmW2ksDDMACCPbTzSTBrwrg05LRGwqEXyiNs5aXTCw"


def test_exchange_php_reference_vector_matches_node_signature_contract() -> None:
    assert address_from_public_key(PUBLIC_KEY) == SENDER
    assert canonical_json(UNSIGNED_TRANSFER) == CANONICAL_UNSIGNED_TRANSFER
    assert transaction_hash(UNSIGNED_TRANSFER, PUBLIC_KEY) == TX_HASH
    assert matching_transaction_signature_payload(UNSIGNED_TRANSFER, PUBLIC_KEY, TX_HASH) == UNSIGNED_TRANSFER
    assert verify_payload_signature(PUBLIC_KEY, UNSIGNED_TRANSFER, SIGNATURE)


def test_exchange_php_reference_must_sign_empty_payload_as_object() -> None:
    wrong_payload = {**UNSIGNED_TRANSFER, "payload": []}

    assert canonical_json(wrong_payload) != CANONICAL_UNSIGNED_TRANSFER
    assert transaction_hash(wrong_payload, PUBLIC_KEY) != TX_HASH
    assert not verify_payload_signature(PUBLIC_KEY, wrong_payload, SIGNATURE)


def test_exchange_php_reference_exposes_balance_helper() -> None:
    source = (Path(__file__).resolve().parents[1] / "deploy/php/picoin_exchange_client.php").read_text(
        encoding="utf-8"
    )

    assert "function get_picoin_balance(" in source
    assert "'http://127.0.0.1:8000'" in source
    assert "'/wallet/balance/'" in source
