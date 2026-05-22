"""Tests for wallet transaction submission flow (web3 → mempool)."""

import json
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.core.crypto import canonical_json
from app.core.money import canonical_amount, to_units
from app.core.settings import NETWORK_ID, CHAIN_ID, MIN_TX_FEE_UNITS
from app.core.signatures import generate_keypair
from app.db.database import get_connection, init_db
from app.services.network import register_peer
from app.services.wallet import address_from_public_key, transaction_hash, unsigned_transaction_payload
from app.core.signatures import sign_payload, verify_payload_signature


def _build_test_client(tmp_path, monkeypatch) -> TestClient:
    """Build a test client with fresh database."""
    db_path = tmp_path / "test_wallet_tx_flow.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    init_db(db_path)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_network_config_endpoint_returns_expected_network_id(tmp_path, monkeypatch) -> None:
    """Verify /network/config returns the node's network_id and chain_id."""
    client = _build_test_client(tmp_path, monkeypatch)
    response = client.get("/network/config")

    assert response.status_code == 200
    body = response.json()
    assert body["network_id"] == NETWORK_ID
    assert body["chain_id"] == CHAIN_ID
    assert body["protocol_version"]
    assert "message" in body


def test_transaction_submit_requires_correct_network_id(tmp_path, monkeypatch) -> None:
    """Transaction with wrong network_id should be rejected."""
    client = _build_test_client(tmp_path, monkeypatch)
    
    # Generate a valid wallet keypair
    keypair = generate_keypair()
    sender = address_from_public_key(keypair["public_key"])
    recipient = address_from_public_key(generate_keypair()["public_key"])
    
    # Create a transaction with WRONG network_id
    nonce = 1
    amount_units = to_units("1.0")
    fee_units = to_units("0.001") or MIN_TX_FEE_UNITS
    
    unsigned_payload = unsigned_transaction_payload(
        tx_type="transfer",
        sender=sender,
        recipient=recipient,
        amount=canonical_amount(amount_units),
        nonce=nonce,
        fee=canonical_amount(fee_units),
        payload={},
        timestamp=datetime.now(timezone.utc).isoformat(),
        network_id="wrong-network",  # WRONG!
        chain_id=CHAIN_ID,
    )
    
    signature = sign_payload(keypair["private_key"], unsigned_payload)
    tx_hash = transaction_hash(unsigned_payload, keypair["public_key"])
    
    tx_payload = {
        **unsigned_payload,
        "public_key": keypair["public_key"],
        "signature": signature,
        "tx_hash": tx_hash,
    }
    
    response = client.post("/tx/submit", json=tx_payload)
    
    # Should be rejected with 409 (Conflict)
    assert response.status_code == 409
    assert "network" in response.json()["detail"].lower() or "chain" in response.json()["detail"].lower()


def test_transaction_submit_requires_correct_chain_id(tmp_path, monkeypatch) -> None:
    """Transaction with wrong chain_id should be rejected."""
    client = _build_test_client(tmp_path, monkeypatch)
    
    keypair = generate_keypair()
    sender = address_from_public_key(keypair["public_key"])
    recipient = address_from_public_key(generate_keypair()["public_key"])
    
    nonce = 1
    amount_units = to_units("1.0")
    fee_units = to_units("0.001") or MIN_TX_FEE_UNITS
    
    unsigned_payload = unsigned_transaction_payload(
        tx_type="transfer",
        sender=sender,
        recipient=recipient,
        amount=canonical_amount(amount_units),
        nonce=nonce,
        fee=canonical_amount(fee_units),
        payload={},
        timestamp=datetime.now(timezone.utc).isoformat(),
        network_id=NETWORK_ID,
        chain_id="wrong-chain-id-v001",  # WRONG!
    )
    
    signature = sign_payload(keypair["private_key"], unsigned_payload)
    tx_hash = transaction_hash(unsigned_payload, keypair["public_key"])
    
    tx_payload = {
        **unsigned_payload,
        "public_key": keypair["public_key"],
        "signature": signature,
        "tx_hash": tx_hash,
    }
    
    response = client.post("/tx/submit", json=tx_payload)
    
    assert response.status_code == 409
    assert "network" in response.json()["detail"].lower() or "chain" in response.json()["detail"].lower()


def test_transaction_submit_and_retrieval_with_correct_network_chain(tmp_path, monkeypatch) -> None:
    """Valid transaction with correct network_id and chain_id should be accepted and retrievable."""
    client = _build_test_client(tmp_path, monkeypatch)
    
    # Ensure we can send to ourselves for testing
    keypair = generate_keypair()
    sender = address_from_public_key(keypair["public_key"])
    recipient = sender  # Send to self for simplicity
    
    nonce = 1
    amount_units = to_units("1.0")
    fee_units = to_units("0.001") or MIN_TX_FEE_UNITS
    
    unsigned_payload = unsigned_transaction_payload(
        tx_type="transfer",
        sender=sender,
        recipient=recipient,
        amount=canonical_amount(amount_units),
        nonce=nonce,
        fee=canonical_amount(fee_units),
        payload={},
        timestamp=datetime.now(timezone.utc).isoformat(),
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,  # CORRECT!
    )
    
    signature = sign_payload(keypair["private_key"], unsigned_payload)
    tx_hash = transaction_hash(unsigned_payload, keypair["public_key"])
    
    tx_payload = {
        **unsigned_payload,
        "public_key": keypair["public_key"],
        "signature": signature,
        "tx_hash": tx_hash,
    }
    
    # Submit transaction
    submit_response = client.post("/tx/submit", json=tx_payload)
    assert submit_response.status_code == 201
    submitted = submit_response.json()
    assert submitted["tx_hash"] == tx_hash
    assert submitted["status"] == "pending"
    
    # Retrieve transaction by hash
    get_response = client.get(f"/tx/{tx_hash}")
    assert get_response.status_code == 200
    retrieved = get_response.json()
    assert retrieved["tx_hash"] == tx_hash
    assert retrieved["status"] == "pending"
    assert retrieved["sender"] == sender
    assert retrieved["recipient"] == recipient


def test_duplicate_nonce_is_rejected(tmp_path, monkeypatch) -> None:
    """Two transactions from same sender with same nonce should be rejected."""
    client = _build_test_client(tmp_path, monkeypatch)
    
    keypair = generate_keypair()
    sender = address_from_public_key(keypair["public_key"])
    recipient = address_from_public_key(generate_keypair()["public_key"])
    
    nonce = 1
    amount_units = to_units("1.0")
    fee_units = to_units("0.001") or MIN_TX_FEE_UNITS
    
    # Build and submit first transaction
    unsigned_payload_1 = unsigned_transaction_payload(
        tx_type="transfer",
        sender=sender,
        recipient=recipient,
        amount=canonical_amount(amount_units),
        nonce=nonce,
        fee=canonical_amount(fee_units),
        payload={},
        timestamp=datetime.now(timezone.utc).isoformat(),
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
    )
    
    signature_1 = sign_payload(keypair["private_key"], unsigned_payload_1)
    tx_hash_1 = transaction_hash(unsigned_payload_1, keypair["public_key"])
    
    tx_payload_1 = {
        **unsigned_payload_1,
        "public_key": keypair["public_key"],
        "signature": signature_1,
        "tx_hash": tx_hash_1,
    }
    
    response_1 = client.post("/tx/submit", json=tx_payload_1)
    assert response_1.status_code == 201
    
    # Try to submit second transaction with same nonce but different recipient
    recipient_2 = address_from_public_key(generate_keypair()["public_key"])
    unsigned_payload_2 = unsigned_transaction_payload(
        tx_type="transfer",
        sender=sender,
        recipient=recipient_2,
        amount=canonical_amount(amount_units),
        nonce=nonce,  # SAME nonce!
        fee=canonical_amount(fee_units),
        payload={},
        timestamp=datetime.now(timezone.utc).isoformat(),
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
    )
    
    signature_2 = sign_payload(keypair["private_key"], unsigned_payload_2)
    tx_hash_2 = transaction_hash(unsigned_payload_2, keypair["public_key"])
    
    tx_payload_2 = {
        **unsigned_payload_2,
        "public_key": keypair["public_key"],
        "signature": signature_2,
        "tx_hash": tx_hash_2,
    }
    
    response_2 = client.post("/tx/submit", json=tx_payload_2)
    
    # Should be rejected with 409 (duplicate nonce)
    assert response_2.status_code == 409
    assert "nonce" in response_2.json()["detail"].lower()


def test_mempool_status_shows_pending_transactions(tmp_path, monkeypatch) -> None:
    """After submitting a transaction, mempool/status should show it as pending."""
    client = _build_test_client(tmp_path, monkeypatch)
    
    keypair = generate_keypair()
    sender = address_from_public_key(keypair["public_key"])
    recipient = sender
    
    nonce = 1
    amount_units = to_units("1.0")
    fee_units = to_units("0.001") or MIN_TX_FEE_UNITS
    
    unsigned_payload = unsigned_transaction_payload(
        tx_type="transfer",
        sender=sender,
        recipient=recipient,
        amount=canonical_amount(amount_units),
        nonce=nonce,
        fee=canonical_amount(fee_units),
        payload={},
        timestamp=datetime.now(timezone.utc).isoformat(),
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
    )
    
    signature = sign_payload(keypair["private_key"], unsigned_payload)
    tx_hash = transaction_hash(unsigned_payload, keypair["public_key"])
    
    tx_payload = {
        **unsigned_payload,
        "public_key": keypair["public_key"],
        "signature": signature,
        "tx_hash": tx_hash,
    }
    
    # Check initial state
    status_before = client.get("/mempool/status").json()
    assert status_before["pending_count"] == 0
    
    # Submit transaction
    submit_response = client.post("/tx/submit", json=tx_payload)
    assert submit_response.status_code == 201
    
    # Check mempool status
    status_after = client.get("/mempool/status").json()
    assert status_after["pending_count"] == 1


def test_transaction_submit_marks_origin_tx_propagated_after_peer_accept(tmp_path, monkeypatch) -> None:
    """Origin node should persist propagated=1 after at least one peer accepts the transaction."""
    client = _build_test_client(tmp_path, monkeypatch)

    # Register a peer so gossip_json has a target to send to.
    register_peer(
        node_id="peer-node-1",
        peer_address="https://peer1.example.com",
        peer_type="full",
        protocol_version="1.0",
    )

    keypair = generate_keypair()
    sender = address_from_public_key(keypair["public_key"])
    recipient = sender
    nonce = 1
    amount_units = to_units("1.0")
    fee_units = to_units("0.001") or MIN_TX_FEE_UNITS

    unsigned_payload = unsigned_transaction_payload(
        tx_type="transfer",
        sender=sender,
        recipient=recipient,
        amount=canonical_amount(amount_units),
        nonce=nonce,
        fee=canonical_amount(fee_units),
        payload={},
        timestamp=datetime.now(timezone.utc).isoformat(),
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
    )
    signature = sign_payload(keypair["private_key"], unsigned_payload)
    tx_hash = transaction_hash(unsigned_payload, keypair["public_key"])

    tx_payload = {
        **unsigned_payload,
        "public_key": keypair["public_key"],
        "signature": signature,
        "tx_hash": tx_hash,
    }

    class DummyResponse:
        status_code = 200
        ok = True

        def json(self):
            return {"accepted": True}

        def raise_for_status(self):
            return None

    def fake_post(url, json=None, timeout=None):
        return DummyResponse()

    monkeypatch.setattr("app.services.network.requests.post", fake_post)

    response = client.post("/tx/submit", json=tx_payload)
    assert response.status_code == 201
    assert response.json()["tx_hash"] == tx_hash

    with get_connection() as connection:
        row = connection.execute(
            "SELECT propagated, status FROM mempool_transactions WHERE tx_hash = ?",
            (tx_hash,),
        ).fetchone()
    assert row is not None
    assert int(row["propagated"]) == 1
    assert row["status"] == "pending"
