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
from app.services.mining import register_miner
from app.services.network import register_peer
from app.services.wallet import (
    address_from_public_key,
    matching_transaction_signature_payload,
    transaction_hash,
    unsigned_transaction_payload,
)
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


def test_recent_transactions_endpoint_returns_recent_activity(tmp_path, monkeypatch) -> None:
    """ /transactions/recent should return recent transactions ordered by latest activity. """
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

    submit_response = client.post("/tx/submit", json=tx_payload)
    assert submit_response.status_code == 201

    recent_response = client.get("/transactions/recent?limit=5")
    assert recent_response.status_code == 200
    recent = recent_response.json()
    assert isinstance(recent, list)
    assert any(tx["tx_hash"] == tx_hash for tx in recent)
    assert recent[0]["tx_hash"] == tx_hash

    filtered_response = client.get(f"/transactions/recent?address={sender}&limit=5")
    assert filtered_response.status_code == 200
    filtered = filtered_response.json()
    assert any(tx["tx_hash"] == tx_hash for tx in filtered)

    status_response = client.get("/transactions/recent?status=pending&limit=5")
    assert status_response.status_code == 200
    status_list = status_response.json()
    assert all(tx["status"] == "pending" for tx in status_list)


def test_address_transaction_history_returns_confirmed_transfer(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)

    sender_keypair = generate_keypair()
    sender = address_from_public_key(sender_keypair["public_key"])
    recipient = address_from_public_key(generate_keypair()["public_key"])
    amount_units = to_units("0.25")
    fee_units = to_units("0.001")
    timestamp = "2026-06-14T17:30:00+00:00"
    unsigned_payload = unsigned_transaction_payload(
        tx_type="transfer",
        sender=sender,
        recipient=recipient,
        amount=canonical_amount(amount_units),
        nonce=1,
        fee=canonical_amount(fee_units),
        payload={},
        timestamp=timestamp,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
    )
    tx_hash = transaction_hash(unsigned_payload, sender_keypair["public_key"])
    signature = sign_payload(sender_keypair["private_key"], unsigned_payload)
    miner = register_miner("history-miner", generate_keypair()["public_key"])

    with get_connection() as connection:
        protocol_params_id = connection.execute("SELECT id FROM protocol_params WHERE active = 1").fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at
            )
            VALUES ('task_tx_history', ?, 1, 64, 'bbp_hex_v1', 'accepted', ?, ?)
            """,
            (miner["miner_id"], protocol_params_id, timestamp),
        )
        connection.execute(
            """
            INSERT INTO blocks (
                height, previous_hash, miner_id, range_start, range_end, algorithm,
                result_hash, samples, timestamp, block_hash, reward, reward_units,
                difficulty, task_id, protocol_params_id, protocol_version, validation_mode
            )
            VALUES (7, ?, ?, 1, 64, 'bbp_hex_v1', ?, '[]', ?, ?, 0, 0, 1.0,
                    'task_tx_history', ?, '1.0', 'external_commit_reveal')
            """,
            ("0" * 64, miner["miner_id"], "a" * 64, timestamp, "b" * 64, protocol_params_id),
        )
        connection.execute(
            """
            INSERT INTO mempool_transactions (
                tx_hash, tx_type, sender, recipient, amount, amount_units, nonce, fee, fee_units,
                payload, public_key, signature, status, propagated, block_height,
                confirmed_at, expires_at, created_at, updated_at
            )
            VALUES (?, 'transfer', ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 'confirmed', 0, 7, ?, ?, ?, ?)
            """,
            (
                tx_hash,
                sender,
                recipient,
                canonical_amount(amount_units),
                amount_units,
                canonical_amount(fee_units),
                fee_units,
                json.dumps(unsigned_payload, sort_keys=True, separators=(",", ":")),
                sender_keypair["public_key"],
                signature,
                timestamp,
                "2026-06-14T18:30:00+00:00",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, amount_units, balance_after, balance_after_units,
                entry_type, block_height, related_id, description, created_at
            )
            VALUES (?, 'wallet', ?, ?, 9.749, ?, 'transfer_debit', 7, ?, ?, ?)
            """,
            (
                sender,
                canonical_amount(-(amount_units + fee_units)),
                -(amount_units + fee_units),
                to_units("9.749"),
                tx_hash,
                f"transfer debit to {recipient}",
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, amount_units, balance_after, balance_after_units,
                entry_type, block_height, related_id, description, created_at
            )
            VALUES (?, 'wallet', ?, ?, 0.25, ?, 'transfer_credit', 7, ?, ?, ?)
            """,
            (
                recipient,
                canonical_amount(amount_units),
                amount_units,
                amount_units,
                tx_hash,
                f"transfer credit from {sender}",
                timestamp,
            ),
        )

    response = client.get(f"/transactions/history?address={recipient}&limit=5")
    assert response.status_code == 200
    history = response.json()
    assert history[0]["tx_hash"] == tx_hash
    assert history[0]["tx_type"] == "transfer"
    assert history[0]["direction"] == "in"
    assert history[0]["sender"] == sender
    assert history[0]["recipient"] == recipient
    assert history[0]["amount"] == "0.250000"
    assert history[0]["fee"] == "0.001000"
    assert history[0]["status"] == "confirmed"
    assert history[0]["block_height"] == 7
    assert history[0]["confirmations"] == 1

    alias_response = client.get(f"/wallet/{recipient}/transactions?limit=5")
    assert alias_response.status_code == 200
    assert alias_response.json()[0]["tx_hash"] == tx_hash


def test_address_transaction_history_returns_snapshot_import_event(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)

    address = address_from_public_key(generate_keypair()["public_key"])
    snapshot_hash = "c" * 64
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, amount_units, balance_after, balance_after_units,
                entry_type, block_height, related_id, description, created_at
            )
            VALUES (?, 'wallet', 0.25, 250000, 0.25, 250000, 'snapshot_state_import',
                    16414, ?, 'canonical snapshot state import', '2026-06-14T17:28:10+00:00')
            """,
            (address, snapshot_hash),
        )

    response = client.get(f"/transactions/history?address={address}&limit=5")
    assert response.status_code == 200
    history = response.json()
    assert history[0]["tx_hash"] is None
    assert history[0]["related_id"] == snapshot_hash
    assert history[0]["tx_type"] == "snapshot_state_import"
    assert history[0]["status"] == "confirmed"
    assert history[0]["direction"] == "in"
    assert history[0]["amount"] == "0.250000"
    assert "snapshot" in history[0]["note"]


def test_address_transaction_history_backfills_verified_peer_history(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)

    recipient = address_from_public_key(generate_keypair()["public_key"])
    sender = address_from_public_key(generate_keypair()["public_key"])
    tx_hash = "a" * 64
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO miners (miner_id, name, public_key, registered_at)
            VALUES ('miner-history', 'miner-history', 'ed25519:test-history', '2026-06-14T16:30:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status, created_at
            )
            VALUES ('task-history', 'miner-history', 1, 2, 'bbp_hex_v1', 'accepted',
                    '2026-06-14T16:30:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO blocks (
                height, previous_hash, miner_id, range_start, range_end, algorithm,
                result_hash, samples, timestamp, block_hash, reward, task_id, tx_hashes, tx_count
            )
            VALUES (10, ?, 'miner-history', 1, 2, 'bbp_hex_v1',
                    ?, '[]', '2026-06-14T16:37:39+00:00', ?, 3.1416, 'task-history', ?, 1)
            """,
            ("0" * 64, "b" * 64, "c" * 64, json.dumps([tx_hash])),
        )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return [
                {
                    "tx_hash": tx_hash,
                    "tx_type": "transfer",
                    "sender": sender,
                    "recipient": recipient,
                    "amount": 0.15,
                    "amount_units": 150000,
                    "fee": 0.001,
                    "fee_units": 1000,
                    "status": "confirmed",
                    "nonce": 2,
                    "block_height": 10,
                    "timestamp": "2026-06-14T16:35:19Z",
                    "confirmed_at": "2026-06-14T16:37:39Z",
                    "created_at": "2026-06-14T16:35:19Z",
                    "updated_at": "2026-06-14T16:37:39Z",
                }
            ]

    requested: list[tuple[str, dict]] = []

    def fake_get(url: str, *, params: dict, timeout: float) -> FakeResponse:
        requested.append((url, params))
        return FakeResponse()

    monkeypatch.setattr("app.services.network.BOOTSTRAP_PEERS", ["https://api.picoin.science"])
    monkeypatch.setattr("app.services.network.HISTORY_BACKFILL_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("app.services.network.requests.get", fake_get)

    response = client.get(f"/transactions/history?address={recipient}&limit=5")
    assert response.status_code == 200
    history = response.json()
    assert requested
    assert history[0]["tx_hash"] == tx_hash
    assert history[0]["source"] == "history_cache"
    assert history[0]["direction"] == "in"
    assert history[0]["amount"] == "0.150000"
    assert history[0]["block_height"] == 10
    assert history[0]["confirmations"] == 1
    assert history[0]["verified_local_inclusion"] is True
    assert history[0]["archival_peer_backfill"] is False


def test_address_transaction_history_ignores_incompatible_history_peer(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)

    address = address_from_public_key(generate_keypair()["public_key"])

    def fake_get(url: str, *, params: dict, timeout: float) -> object:
        raise RuntimeError("old peer does not support transaction history")

    monkeypatch.setattr("app.services.network.BOOTSTRAP_PEERS", ["https://old-node.example"])
    monkeypatch.setattr("app.services.network.HISTORY_BACKFILL_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("app.services.network.requests.get", fake_get)

    response = client.get(f"/transactions/history?address={address}&limit=5")
    assert response.status_code == 200
    assert response.json() == []


def test_address_transaction_history_backfills_pre_snapshot_archival_history(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)

    recipient = address_from_public_key(generate_keypair()["public_key"])
    sender = address_from_public_key(generate_keypair()["public_key"])
    tx_hash = "d" * 64
    snapshot_hash = "e" * 64
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO miners (miner_id, name, public_key, registered_at)
            VALUES ('miner-snapshot', 'miner-snapshot', 'ed25519:test-snapshot', '2026-06-14T17:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status, created_at
            )
            VALUES ('task-snapshot', 'miner-snapshot', 1, 2, 'bbp_hex_v1', 'accepted',
                    '2026-06-14T17:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO blocks (
                height, previous_hash, miner_id, range_start, range_end, algorithm,
                result_hash, samples, timestamp, block_hash, reward, task_id, tx_hashes, tx_count
            )
            VALUES (10, ?, 'miner-snapshot', 1, 2, 'bbp_hex_v1',
                    ?, '[]', '2026-06-14T17:00:00+00:00', ?, 3.1416, 'task-snapshot', '[]', 0)
            """,
            ("0" * 64, "f" * 64, "1" * 64),
        )
        connection.execute(
            """
            INSERT INTO canonical_snapshot_imports (
                import_id, height, block_hash, previous_hash, state_root, balances_hash,
                snapshot_hash, balances_count, total_balance, total_balance_units,
                source, active, activated_at, state_applied, state_applied_at,
                imported_at, verified_at, payload
            )
            VALUES ('snapshot-import', 10, ?, ?, ?, ?, ?, 1, 0.25, 250000,
                    'test', 1, '2026-06-14T17:00:00+00:00', 1,
                    '2026-06-14T17:00:00+00:00', '2026-06-14T17:00:00+00:00',
                    '2026-06-14T17:00:00+00:00', '{}')
            """,
            ("1" * 64, "0" * 64, "2" * 64, "3" * 64, snapshot_hash),
        )
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, amount_units, balance_after, balance_after_units,
                entry_type, block_height, related_id, description, created_at
            )
            VALUES (?, 'wallet', 0.25, 250000, 0.25, 250000, 'snapshot_state_import',
                    10, ?, 'canonical snapshot state import', '2026-06-14T17:00:00+00:00')
            """,
            (recipient, snapshot_hash),
        )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return [
                {
                    "tx_hash": tx_hash,
                    "tx_type": "transfer",
                    "sender": sender,
                    "recipient": recipient,
                    "amount": 0.25,
                    "amount_units": 250000,
                    "fee": 0.001,
                    "fee_units": 1000,
                    "status": "confirmed",
                    "nonce": 1,
                    "block_height": 8,
                    "timestamp": "2026-06-14T16:33:09Z",
                    "confirmed_at": "2026-06-14T16:33:39Z",
                    "created_at": "2026-06-14T16:33:09Z",
                    "updated_at": "2026-06-14T16:33:39Z",
                }
            ]

    monkeypatch.setattr("app.services.network.BOOTSTRAP_PEERS", ["https://api.picoin.science"])
    monkeypatch.setattr("app.services.network.HISTORY_BACKFILL_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("app.services.network.requests.get", lambda *args, **kwargs: FakeResponse())

    response = client.get(f"/transactions/history?address={recipient}&limit=5")
    assert response.status_code == 200
    history = response.json()
    assert len(history) == 1
    assert history[0]["tx_hash"] == tx_hash
    assert history[0]["source"] == "history_cache"
    assert history[0]["verified_local_inclusion"] is False
    assert history[0]["archival_peer_backfill"] is True
    assert history[0]["confirmations"] == 3
    assert "pre-snapshot" in history[0]["note"]


def test_wallet_balance_returns_zero_for_unused_valid_address(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)

    address = address_from_public_key(generate_keypair()["public_key"])

    response = client.get(f"/wallet/balance/{address.lower()}")
    assert response.status_code == 200
    balance = response.json()
    assert balance["address"] == address
    assert balance["account_id"] == address
    assert balance["account_type"] == "wallet"
    assert balance["balance"] == 0.0
    assert balance["balance_units"] == 0
    assert balance["available_balance"] == 0.0
    assert balance["total_balance"] == 0.0
    assert balance["updated_at"] is None


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


def test_transaction_signature_payload_accepts_replayed_utc_z_timestamp() -> None:
    unsigned_payload = unsigned_transaction_payload(
        tx_type="stake",
        sender="PIEB149E99DCD64653088B68F92D6790068428462919DD96",
        recipient=None,
        amount=canonical_amount(to_units("31.416")),
        nonce=1,
        fee=canonical_amount(to_units("0.001")),
        payload={"stake_type": "validator", "validator_id": "validator_821444ca8baa47a5"},
        timestamp="2026-06-01T18:30:19.283682Z",
        network_id="picoin-mainnet-v1",
        chain_id=314159,
    )
    public_key = "ed25519:RoafWOwI4BjqCUPM4L6rWaegjmwem1uw_leSkqQ2zuc"
    signature = "brExnXn790OXjJQVWKkzJf_3V-zetT-CvmIPvk0KYB7BEylmJiOX_Iz0Nz4bZe1Wqz69CwBsDUX9z5ChBXu-BA"

    signature_payload = matching_transaction_signature_payload(
        unsigned_payload,
        public_key,
        "9d343e68ced1c72120dcd1d824dc5a74f5d9ffd18ea5b0a75e983dc3af76cabd",
    )

    assert signature_payload is not None
    assert signature_payload["timestamp"] == "2026-06-01T18:30:19.283682+00:00"
    assert verify_payload_signature(public_key, signature_payload, signature) is True


def test_transaction_signature_payload_accepts_replayed_trimmed_fraction_timestamp() -> None:
    unsigned_payload = unsigned_transaction_payload(
        tx_type="transfer",
        sender="PIBFF3E7EC720092297B7A9F4CCDB4A588D8814F860E78B8",
        recipient="PI7504E598E4A6769E0B34C3A8A9BCC36D5CBAEC7019950D",
        amount=canonical_amount(to_units("31.5")),
        nonce=1,
        fee=canonical_amount(to_units("0.001")),
        payload={},
        timestamp="2026-06-01T21:44:29.639000Z",
        network_id="picoin-mainnet-v1",
        chain_id=314159,
    )
    public_key = "ed25519:yqRfRJ8tdJfGQzFIlq7SfoC6A6ILLjnJRus_FaKSmkA"
    signature = "186GxGNHiJDo0jhDF15H0ZmaZxTs42xn5oMqckGugIzUJlnCFzebWN5XW_1uFPr57gnmauElOICNUpPGjmhxAA"

    signature_payload = matching_transaction_signature_payload(
        unsigned_payload,
        public_key,
        "83f41f39b26b3dfc7faa541ad26f1af1c2afa6a65cc2414edd101c5723c0da1b",
    )

    assert signature_payload is not None
    assert signature_payload["timestamp"] == "2026-06-01T21:44:29.639Z"
    assert verify_payload_signature(public_key, signature_payload, signature) is True


def test_transaction_signature_payload_accepts_legacy_numeric_money_payload() -> None:
    keypair = generate_keypair()
    sender = address_from_public_key(keypair["public_key"])
    legacy_payload = {
        "amount": 2.5,
        "chain_id": str(CHAIN_ID),
        "fee": 0.001,
        "network_id": NETWORK_ID,
        "nonce": 1,
        "payload": {},
        "sender": sender,
        "timestamp": "2026-06-02T20:00:00+00:00",
        "tx_type": "stake",
    }
    signature = sign_payload(keypair["private_key"], legacy_payload)
    tx_hash = transaction_hash(legacy_payload, keypair["public_key"])
    replay_payload = unsigned_transaction_payload(
        tx_type="stake",
        sender=sender,
        recipient=None,
        amount=canonical_amount(to_units("2.5")),
        nonce=1,
        fee=canonical_amount(to_units("0.001")),
        payload={},
        timestamp="2026-06-02T20:00:00Z",
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
    )

    signature_payload = matching_transaction_signature_payload(
        replay_payload,
        keypair["public_key"],
        tx_hash,
    )

    assert signature_payload == legacy_payload
    assert verify_payload_signature(keypair["public_key"], signature_payload, signature) is True
