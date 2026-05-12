import pytest

from app.core.settings import CHAIN_ID, GENESIS_HASH, NETWORK_ID, PROTOCOL_VERSION
from app.db.database import init_db
from app.services.network import (
    NetworkError,
    get_sync_status,
    heartbeat_peer,
    list_mempool,
    list_peers,
    receive_block_header,
    register_peer,
    submit_transaction,
)
from app.services.wallet import create_wallet, sign_transaction


def _init_network_db(tmp_path, monkeypatch, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)


def test_peer_registry_and_heartbeat(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peers.sqlite3")

    peer = register_peer(
        node_id="validator-1",
        peer_address="http://validator-1:8000",
        peer_type="validator",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    heartbeat = heartbeat_peer(peer["peer_id"])
    peers = list_peers()

    assert peer["status"] == "connected"
    assert heartbeat["peer_id"] == peer["peer_id"]
    assert peers[0]["peer_type"] == "validator"


def test_peer_rejects_wrong_chain(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "wrong-chain.sqlite3")

    with pytest.raises(NetworkError, match="chain_id mismatch"):
        register_peer(
            node_id="bad-peer",
            peer_address="http://bad-peer:8000",
            peer_type="full",
            protocol_version=PROTOCOL_VERSION,
            network_id=NETWORK_ID,
            chain_id="other-chain",
            genesis_hash=GENESIS_HASH,
        )


def test_signed_transaction_enters_mempool_once(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "mempool.sqlite3")

    wallet = create_wallet("alice")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient="PIRECIPIENT",
        amount=1.5,
        nonce=1,
        fee=0.01,
    )

    accepted = submit_transaction(tx)
    duplicate = submit_transaction(tx)
    mempool = list_mempool()

    assert accepted["tx_hash"] == tx["tx_hash"]
    assert duplicate["tx_hash"] == tx["tx_hash"]
    assert mempool[0]["status"] == "pending"
    assert mempool[0]["sender"] == wallet["address"]


def test_duplicate_nonce_is_rejected(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "duplicate-nonce.sqlite3")

    wallet = create_wallet("alice")
    first = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient="PIA",
        amount=1,
        nonce=7,
    )
    second = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient="PIB",
        amount=2,
        nonce=7,
    )

    submit_transaction(first)
    with pytest.raises(NetworkError, match="duplicate sender nonce"):
        submit_transaction(second)


def test_invalid_signature_is_rejected(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "invalid-signature.sqlite3")

    wallet = create_wallet("alice")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient="PIB",
        amount=2,
        nonce=1,
    )
    tx["signature"] = "invalid"

    with pytest.raises(NetworkError, match="invalid transaction signature"):
        submit_transaction(tx)


def test_invalid_next_block_previous_hash_is_rejected(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "invalid-block.sqlite3")

    with pytest.raises(NetworkError, match="previous_hash"):
        receive_block_header(
            {
                "height": 1,
                "previous_hash": "1" * 64,
                "block_hash": "2" * 64,
                "timestamp": "2026-05-12T00:00:00+00:00",
            }
        )


def test_sync_status_reports_distributed_context(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "sync-status.sqlite3")

    status = get_sync_status()

    assert status["chain_id"] == CHAIN_ID
    assert status["network_id"] == NETWORK_ID
    assert status["latest_block_height"] == 0
    assert status["latest_block_hash"] == GENESIS_HASH
