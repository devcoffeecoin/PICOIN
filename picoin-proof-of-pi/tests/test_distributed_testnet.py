import json

import pytest

from app.core.crypto import canonical_json, hash_result
from app.core.pi import calculate_pi_segment
from app.core.settings import CHAIN_ID, GENESIS_HASH, NETWORK_ID, PROTOCOL_VERSION
from app.core.signatures import build_submission_signature_payload, generate_keypair, sign_payload
from app.db.database import get_connection, init_db
from app.models.schemas import SignedTransactionRequest
from app.services.consensus import (
    block_hash_debug,
    debug_block_determinism,
    get_replay_status,
    propose_block,
    replay_finalized_blocks,
)
from app.services.mining import (
    create_next_task,
    get_balance_amount,
    get_block,
    get_full_economic_audit,
    get_health_status,
    register_miner,
    repair_missing_block_rewards,
    submit_task,
    verify_chain,
)
from app.services.network import (
    NetworkError,
    get_blocks_since,
    get_transaction,
    get_sync_status,
    heartbeat_peer,
    list_mempool,
    list_peers,
    receive_block_header,
    reconcile_peer,
    register_peer,
    submit_transaction,
    sync_blocks_until,
)
from app.services.science import get_science_account, get_science_job, get_science_reserve_governance
from app.services.state import (
    activate_imported_snapshot,
    apply_imported_snapshot_state,
    calculate_state_root,
    create_canonical_checkpoint,
    export_canonical_snapshot,
    import_canonical_snapshot,
    latest_checkpoint,
    list_imported_snapshots,
    restore_imported_snapshot_state,
    update_block_state_root,
    validate_snapshot_document,
    verify_checkpoint,
)
from app.services.genesis import genesis_allocations_hash
from app.services.treasury import (
    SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
    get_scientific_development_treasury,
)
from app.services.transactions import get_wallet_nonce_status, select_block_transactions
from app.services.wallet import create_wallet, sign_transaction
from picoin.cli import _snapshot_from_sqlite


def _init_network_db(tmp_path, monkeypatch, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)


def test_canonical_json_serialization_is_stable() -> None:
    payload = {"z": 1, "a": {"b": 2, "a": 1}, "list": [{"y": 2, "x": 1}]}

    encoded = canonical_json(payload)

    assert encoded == '{"a":{"a":1,"b":2},"list":[{"x":1,"y":2}],"z":1}'
    assert " " not in encoded
    assert canonical_json(json.loads(encoded)) == encoded


def test_block_hash_debug_accepts_historical_fraud_field_schema(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "hash-schema-fraud-fields.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("hash-schema-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    block = get_blocks_since(0)["blocks"][0]
    block.pop("fraudulent", None)
    block.pop("fraud_reason", None)
    block.pop("fraud_detected_at", None)

    debug = block_hash_debug(block)

    assert debug["matched"] is True
    assert debug["matched_variant"] is not None


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


def test_receive_block_header_queues_tip_mismatch_for_ancestor_sync(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "header-tip-mismatch.sqlite3")
    block = {
        "height": 1,
        "previous_hash": "f" * 64,
        "block_hash": "a" * 64,
        "timestamp": "2026-05-12T00:00:00+00:00",
    }

    received = receive_block_header(block, source_peer_id="peer-a")

    assert received["status"] == "pending_missing_ancestors"
    assert "previous_hash" in received["reason"]


def test_sync_status_reports_replay_queue_metrics(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "sync-replay-metrics.sqlite3")
    block = {
        "height": 1,
        "previous_hash": "f" * 64,
        "block_hash": "a" * 64,
        "timestamp": "2026-05-12T00:00:00+00:00",
    }

    receive_block_header(block, source_peer_id="peer-a")
    sync_status = get_sync_status()
    replay_status = get_replay_status()

    assert sync_status["pending_replay_blocks"] == 1
    assert sync_status["replay"]["queue_size"] == 1
    assert sync_status["replay"]["header_queue_size"] == 1
    assert replay_status["queue_size"] == 1


def test_sync_blocks_skips_replay_when_backlog_is_high(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "sync-replay-throttle.sqlite3")
    block = {
        "height": 1,
        "previous_hash": "f" * 64,
        "block_hash": "a" * 64,
        "timestamp": "2026-05-12T00:00:00+00:00",
    }
    receive_block_header(block, source_peer_id="peer-a")

    class EmptyBlocksResponse:
        def json(self) -> dict:
            return {"blocks": []}

    monkeypatch.setattr("app.services.network.REPLAY_BACKLOG_THRESHOLD", 0)
    monkeypatch.setattr("app.services.network.requests.get", lambda *args, **kwargs: EmptyBlocksResponse())

    result = sync_blocks_until("http://peer-a:8000", limit=10)

    assert result["replay"]["status"] == "skipped"
    assert result["replay"]["reason"] == "replay backlog above threshold"
    assert result["replay"]["queue_size"] == 1


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


def test_peer_accepts_network_id_drift_when_chain_and_genesis_match(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "network-drift.sqlite3")

    peer = register_peer(
        node_id="drift-peer",
        peer_address="http://drift-peer:8000",
        peer_type="validator",
        protocol_version=PROTOCOL_VERSION,
        network_id="public-testnet-restarted",
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )

    assert peer["status"] == "connected"
    assert peer["network_id"] == NETWORK_ID
    assert peer["metadata"]["observed_network_id"] == "public-testnet-restarted"


def test_signed_transaction_enters_mempool_once(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "mempool.sqlite3")

    wallet = create_wallet("alice")
    recipient = create_wallet("bob")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=recipient["address"],
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
    first_recipient = create_wallet("bob")
    second_recipient = create_wallet("carol")
    first = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=first_recipient["address"],
        amount=1,
        nonce=7,
    )
    second = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=second_recipient["address"],
        amount=2,
        nonce=7,
    )

    submit_transaction(first)
    with pytest.raises(NetworkError, match="duplicate sender nonce"):
        submit_transaction(second)


def test_invalid_signature_is_rejected(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "invalid-signature.sqlite3")

    wallet = create_wallet("alice")
    recipient = create_wallet("bob")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=recipient["address"],
        amount=2,
        nonce=1,
    )
    tx["signature"] = "invalid"

    with pytest.raises(NetworkError, match="invalid transaction signature"):
        submit_transaction(tx)


def test_signed_transaction_request_preserves_signed_timestamp(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "signed-timestamp.sqlite3")

    wallet = create_wallet("alice")
    recipient = create_wallet("bob")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=recipient["address"],
        amount=1,
        nonce=1,
        timestamp="2026-05-14T12:00:00+00:00",
    )

    payload = SignedTransactionRequest(**tx).model_dump(mode="json")

    assert payload["timestamp"] == tx["timestamp"]
    submit_transaction(payload)


def test_nonce_zero_is_rejected_at_submission(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "nonce-zero.sqlite3")

    wallet = create_wallet("alice")
    recipient = create_wallet("bob")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=recipient["address"],
        amount=1,
        nonce=0,
    )

    with pytest.raises(NetworkError, match="nonce must be >= 1"):
        submit_transaction(tx)


def test_invalid_transfer_recipient_is_rejected_at_submission(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "invalid-recipient.sqlite3")

    wallet = create_wallet("alice")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient="PIB",
        amount=1,
        nonce=1,
    )

    with pytest.raises(NetworkError, match="valid PI recipient"):
        submit_transaction(tx)


def test_next_block_previous_hash_mismatch_waits_for_ancestor_sync(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "invalid-block.sqlite3")

    received = receive_block_header(
        {
            "height": 1,
            "previous_hash": "1" * 64,
            "block_hash": "2" * 64,
            "timestamp": "2026-05-12T00:00:00+00:00",
        }
    )

    assert received["status"] == "pending_missing_ancestors"
    assert "previous_hash" in received["reason"]


def test_sync_status_reports_distributed_context(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "sync-status.sqlite3")

    status = get_sync_status()

    assert status["chain_id"] == CHAIN_ID
    assert status["network_id"] == NETWORK_ID
    assert status["latest_block_height"] == 0
    assert status["latest_block_hash"] == GENESIS_HASH


def test_mined_block_confirms_signed_transfer_with_transaction_merkle_root(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "block-transactions.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("tx-miner", miner_key["public_key"])
    sender = create_wallet("alice")
    recipient = create_wallet("bob")
    _fund_wallet_from_genesis(sender["address"], 2.0)

    tx = sign_transaction(
        private_key=sender["private_key"],
        public_key=sender["public_key"],
        tx_type="transfer",
        sender=sender["address"],
        recipient=recipient["address"],
        amount=1.0,
        nonce=1,
        fee=0.01,
    )
    submit_transaction(tx)

    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    block = get_block(1)
    confirmed = get_transaction(tx["tx_hash"])
    chain = verify_chain()

    assert block is not None
    assert block["tx_count"] == 1
    assert block["tx_hashes"] == [tx["tx_hash"]]
    assert block["tx_merkle_root"]
    assert len(block["state_root"]) == 64
    assert block["fee_reward"] == pytest.approx(0.01)
    assert confirmed is not None
    assert confirmed["status"] == "confirmed"
    assert confirmed["block_height"] == 1
    assert get_balance_amount(sender["address"]) == pytest.approx(0.99)
    assert get_balance_amount(recipient["address"]) == pytest.approx(1.0)
    assert get_balance_amount(miner["miner_id"]) == pytest.approx(2.104872 + 0.01)
    assert chain["valid"] is True


def test_wallet_nonce_status_tracks_pending_and_confirmed_transactions(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "wallet-nonce.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("nonce-miner", miner_key["public_key"])
    sender = create_wallet("alice")
    recipient = create_wallet("bob")
    _fund_wallet_from_genesis(sender["address"], 2.0)

    with get_connection() as connection:
        initial = get_wallet_nonce_status(connection, sender["address"])
    assert initial["confirmed_nonce"] == 0
    assert initial["pending_nonce"] == 0
    assert initial["next_nonce"] == 1

    tx = sign_transaction(
        private_key=sender["private_key"],
        public_key=sender["public_key"],
        tx_type="transfer",
        sender=sender["address"],
        recipient=recipient["address"],
        amount=1.0,
        nonce=1,
        fee=0.01,
    )
    submit_transaction(tx)

    with get_connection() as connection:
        pending = get_wallet_nonce_status(connection, sender["address"])
    assert pending["confirmed_nonce"] == 0
    assert pending["pending_nonce"] == 1
    assert pending["next_nonce"] == 2
    assert pending["pending_count"] == 1

    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    with get_connection() as connection:
        confirmed = get_wallet_nonce_status(connection, sender["address"])
    assert confirmed["confirmed_nonce"] == 1
    assert confirmed["pending_nonce"] == 0
    assert confirmed["next_nonce"] == 2
    assert confirmed["pending_count"] == 0


def test_block_transaction_selection_prioritizes_fee_without_reordering_sender_nonce(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "fee-priority.sqlite3")

    alice = create_wallet("alice")
    bob = create_wallet("bob")
    recipient = create_wallet("recipient")
    _fund_wallet_from_genesis(alice["address"], 5.0)
    _fund_wallet_from_genesis(bob["address"], 5.0)

    alice_first = sign_transaction(
        private_key=alice["private_key"],
        public_key=alice["public_key"],
        tx_type="transfer",
        sender=alice["address"],
        recipient=recipient["address"],
        amount=1.0,
        nonce=1,
        fee=0.01,
    )
    alice_second = sign_transaction(
        private_key=alice["private_key"],
        public_key=alice["public_key"],
        tx_type="transfer",
        sender=alice["address"],
        recipient=recipient["address"],
        amount=1.0,
        nonce=2,
        fee=0.50,
    )
    bob_first = sign_transaction(
        private_key=bob["private_key"],
        public_key=bob["public_key"],
        tx_type="transfer",
        sender=bob["address"],
        recipient=recipient["address"],
        amount=1.0,
        nonce=1,
        fee=0.10,
    )
    submit_transaction(alice_first)
    submit_transaction(alice_second)
    submit_transaction(bob_first)

    with get_connection() as connection:
        selected = select_block_transactions(connection, limit=3)

    assert [tx["tx_hash"] for tx in selected] == [
        bob_first["tx_hash"],
        alice_first["tx_hash"],
        alice_second["tx_hash"],
    ]


def test_state_root_detects_ledger_tampering(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "state-root-tamper.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("state-root-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    block = get_block(1)

    assert block is not None
    assert len(block["state_root"]) == 64
    assert verify_chain()["valid"] is True

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, amount
            FROM ledger_entries
            WHERE block_height = 1 AND entry_type = 'block_reward'
            LIMIT 1
            """
        ).fetchone()
        connection.execute(
            "UPDATE ledger_entries SET amount = ? WHERE id = ?",
            (round(float(row["amount"]) + 0.0001, 8), row["id"]),
        )

    tampered = verify_chain()

    assert tampered["valid"] is False
    assert any(issue["reason"] == "state_root does not match ledger replay" for issue in tampered["issues"])


def test_repair_missing_block_rewards_backfills_legacy_economics(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "repair-missing-rewards.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("repair-reward-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    block = get_block(1)

    assert block is not None
    with get_connection() as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM miners WHERE miner_id = ?", (block["miner_id"],))
        connection.execute("DELETE FROM rewards WHERE block_height = 1")
        connection.execute(
            """
            DELETE FROM ledger_entries
            WHERE block_height = 1 AND entry_type = 'block_reward'
            """
        )
        connection.execute(
            """
            UPDATE balances
            SET balance = ROUND(balance - ?, 8)
            WHERE account_id = ?
            """,
            (block["reward"], block["miner_id"]),
        )
        update_block_state_root(connection, 1, block["timestamp"])

    broken_audit = get_full_economic_audit()
    repaired = repair_missing_block_rewards()
    audit = get_full_economic_audit()
    chain = verify_chain()
    repaired_again = repair_missing_block_rewards()

    assert broken_audit["valid"] is False
    assert repaired["repaired_blocks"] == 1
    assert repaired["miners_restored"] == 1
    assert repaired["rewards_inserted"] == 1
    assert repaired["ledger_entries_inserted"] == 1
    assert repaired["audit_valid"] is True
    assert repaired["chain_valid"] is True
    assert audit["valid"] is True
    assert chain["valid"] is True
    assert repaired_again["repaired_blocks"] == 0


def test_canonical_checkpoint_can_be_created_and_verified(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "canonical-checkpoint.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("checkpoint-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    checkpoint = create_canonical_checkpoint(height=1, source="test")
    latest = latest_checkpoint()
    verification = verify_checkpoint(1)

    assert checkpoint["height"] == 1
    assert checkpoint["block_hash"] == get_block(1)["block_hash"]
    assert checkpoint["state_root"] == get_block(1)["state_root"]
    assert checkpoint["balances_hash"] == checkpoint["state_root"]
    assert len(checkpoint["snapshot_hash"]) == 64
    assert latest["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert verification["valid"] is True


def test_checkpoint_verification_detects_snapshot_tampering(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "canonical-checkpoint-tamper.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("checkpoint-tamper-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    create_canonical_checkpoint(height=1, source="test")

    with get_connection() as connection:
        connection.execute(
            "UPDATE canonical_checkpoints SET state_root = ? WHERE height = 1",
            ("f" * 64,),
        )

    verification = verify_checkpoint(1)

    assert verification["valid"] is False
    assert "state_root mismatch" in verification["issues"]


def test_canonical_snapshot_exports_and_imports_into_fresh_node(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-export-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-export-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)

    assert snapshot["valid"] is True
    assert snapshot["checkpoint"]["height"] == 1
    assert snapshot["balances"]
    assert validate_snapshot_document(snapshot)["valid"] is True

    _init_network_db(tmp_path, monkeypatch, "snapshot-import-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    imports = list_imported_snapshots()

    assert imported["imported"] is True
    assert imported["validation"]["valid"] is True
    assert imports[0]["snapshot_hash"] == snapshot["checkpoint"]["snapshot_hash"]
    assert imports[0]["source"] == "peer-a"


def test_active_snapshot_base_accepts_next_block_header(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-active-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-active-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    next_block = get_block(2)

    _init_network_db(tmp_path, monkeypatch, "snapshot-active-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    active = activate_imported_snapshot(imported["snapshot"]["snapshot_hash"])
    received = receive_block_header(next_block, source_peer_id="peer-a")
    status = get_sync_status()

    assert active["active"] is True
    assert received["status"] == "pending_replay"
    assert received["reason"] == "accepted after active snapshot base"
    assert status["active_snapshot_base"]["height"] == 1


def test_apply_snapshot_state_bootstraps_balances_for_fast_sync(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-apply-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-apply-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    expected_miner_balance = get_balance_amount(miner["miner_id"])

    _init_network_db(tmp_path, monkeypatch, "snapshot-apply-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    applied = apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    status = get_sync_status()
    chain = verify_chain()
    audit = get_full_economic_audit()

    assert applied["applied"] is True
    assert applied["balances_applied"] == snapshot["checkpoint"]["balances_count"]
    assert applied["snapshot"]["state_applied"] is True
    assert status["active_snapshot_base"]["state_applied"] is True
    assert status["effective_latest_block_height"] == 1
    assert status["effective_latest_block_hash"] == snapshot["checkpoint"]["block_hash"]
    assert get_balance_amount(miner["miner_id"]) == pytest.approx(expected_miner_balance)
    assert chain["valid"] is True
    assert chain["latest_block_hash"] == snapshot["checkpoint"]["block_hash"]
    assert audit["valid"] is True
    assert audit["supply"]["economic_base_total"] == pytest.approx(snapshot["checkpoint"]["total_balance"])


def test_restore_snapshot_state_replaces_existing_local_chain(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-restore-source.sqlite3")
    source_key = generate_keypair()
    source_miner = register_miner("snapshot-restore-source-miner", source_key["public_key"])
    _mine_legacy_block(source_miner["miner_id"], source_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)

    _init_network_db(tmp_path, monkeypatch, "snapshot-restore-target.sqlite3")
    local_key = generate_keypair()
    local_miner = register_miner("snapshot-restore-local-miner", local_key["public_key"])
    _mine_legacy_block(local_miner["miner_id"], local_key["private_key"])
    create_canonical_checkpoint(height=1, source="stale-local")
    imported = import_canonical_snapshot(snapshot, source="peer-restore")

    restored = restore_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    status = get_sync_status()
    chain = verify_chain()
    audit = get_full_economic_audit()

    assert restored["applied"] is True
    assert restored["replace_existing"] is True
    assert restored["cleared"]["canonical_checkpoints"] == 1
    assert restored["cleared"]["blocks"] == 1
    assert restored["cleared"]["tasks"] == 1
    assert status["latest_block_height"] == 0
    assert status["effective_latest_block_height"] == 1
    assert status["effective_latest_block_hash"] == snapshot["checkpoint"]["block_hash"]
    assert chain["valid"] is True
    assert chain["latest_block_hash"] == snapshot["checkpoint"]["block_hash"]
    assert audit["valid"] is True

    post_restore_key = generate_keypair()
    post_restore_miner = register_miner("snapshot-restore-next-miner", post_restore_key["public_key"])
    _mine_legacy_block(post_restore_miner["miner_id"], post_restore_key["private_key"])
    next_block = get_block(2)

    assert next_block is not None
    assert next_block["previous_hash"] == snapshot["checkpoint"]["block_hash"]


def test_cli_builds_valid_snapshot_from_sqlite_backup(tmp_path, monkeypatch) -> None:
    source_db_path = tmp_path / "sqlite-snapshot-source.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", source_db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", source_db_path)
    init_db(source_db_path)

    source_key = generate_keypair()
    source_miner = register_miner("sqlite-snapshot-miner", source_key["public_key"])
    _mine_legacy_block(source_miner["miner_id"], source_key["private_key"])

    snapshot = _snapshot_from_sqlite(source_db_path, 1)
    validation = validate_snapshot_document(snapshot)

    assert validation["valid"] is True
    assert snapshot["checkpoint"]["height"] == 1
    assert snapshot["checkpoint"]["block_hash"] == get_block(1)["block_hash"]


def test_init_db_does_not_reinsert_genesis_ledger_after_snapshot_apply(tmp_path, monkeypatch) -> None:
    source_db_path = tmp_path / "snapshot-init-cleanup-source.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", source_db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", source_db_path)
    init_db(source_db_path)

    miner_key = generate_keypair()
    miner = register_miner("snapshot-init-cleanup-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)

    target_db_path = tmp_path / "snapshot-init-cleanup-target.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", target_db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", target_db_path)
    init_db(target_db_path)
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, balance_after, entry_type,
                block_height, related_id, description, created_at
            )
            VALUES ('genesis', 'genesis', ?, ?, 'genesis', 0, 'genesis', 'legacy duplicate genesis', ?)
            """,
            (3.1416, 3.1416, "1970-01-01T00:00:00+00:00"),
        )

    init_db(target_db_path)
    audit = get_full_economic_audit()

    assert audit["valid"] is True
    assert "genesis" not in audit["ledger"]["by_entry_type"]
    assert audit["ledger"]["by_entry_type"]["snapshot_state_import"] == pytest.approx(
        snapshot["checkpoint"]["total_balance"]
    )


def test_genesis_allocations_are_applied_deterministically(tmp_path, monkeypatch) -> None:
    allocation = {
        "version": 1,
        "network_id": NETWORK_ID,
        "chain_id": CHAIN_ID,
        "created_at": "2026-05-14T12:48:51.500763Z",
        "allocations": [
            {
                "account_id": "PI340F7EEA37754C5F9C9ADE84D98F9B4AE10F0E",
                "account_type": "wallet",
                "amount": 1.0,
                "description": "public testnet wallet funding",
            }
        ],
    }
    allocation_file = tmp_path / "genesis.allocations.json"
    allocation_file.write_text(json.dumps(allocation), encoding="utf-8")
    monkeypatch.setattr("app.core.settings.GENESIS_ALLOCATIONS_FILE", str(allocation_file))
    monkeypatch.setattr("app.db.database.GENESIS_ALLOCATIONS_FILE", str(allocation_file))

    first_db = tmp_path / "first.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", first_db)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", first_db)
    init_db(first_db)
    with get_connection() as connection:
        first_root = calculate_state_root(connection, 0, "2026-05-14T12:58:53.705008Z")
        first_wallet = connection.execute(
            "SELECT balance FROM balances WHERE account_id = ?",
            ("PI340F7EEA37754C5F9C9ADE84D98F9B4AE10F0E",),
        ).fetchone()
        first_genesis = connection.execute("SELECT balance FROM balances WHERE account_id = 'genesis'").fetchone()

    second_db = tmp_path / "second.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", second_db)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", second_db)
    init_db(second_db)
    with get_connection() as connection:
        second_root = calculate_state_root(connection, 0, "2026-05-14T12:58:53.705008Z")
        allocation_total = connection.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries WHERE entry_type = 'genesis_allocation'"
        ).fetchone()

    assert first_wallet["balance"] == pytest.approx(1.0)
    assert first_genesis["balance"] == pytest.approx(2.1416)
    assert first_root == second_root
    assert allocation_total["total"] == pytest.approx(1.0)
    assert len(genesis_allocations_hash(allocation)) == 64


def test_reconcile_peer_fetches_blocks_after_active_snapshot_base(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-reconcile-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-reconcile-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)

    _init_network_db(tmp_path, monkeypatch, "snapshot-reconcile-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    activate_imported_snapshot(imported["snapshot"]["snapshot_hash"])

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, timeout):
        if url.endswith("/node/identity"):
            return FakeResponse(
                {
                    "node_id": "peer-a",
                    "peer_address": "http://peer-a:8000",
                    "peer_type": "full",
                    "protocol_version": PROTOCOL_VERSION,
                    "network_id": NETWORK_ID,
                    "chain_id": CHAIN_ID,
                    "genesis_hash": GENESIS_HASH,
                    "bootstrap_peers": [],
                }
            )
        if url.endswith("/node/peers"):
            return FakeResponse([])
        if url.endswith("/mempool?limit=100"):
            return FakeResponse([])
        if url.endswith("/consensus/proposals?limit=100"):
            return FakeResponse(
                [
                    {
                        "proposal_id": "snapshot-covered",
                        "proposer_node_id": "peer-a",
                        "payload": {
                            "height": 1,
                            "previous_hash": snapshot["checkpoint"]["previous_hash"],
                            "block_hash": snapshot["checkpoint"]["block_hash"],
                            "timestamp": "2026-05-12T00:00:00+00:00",
                        },
                    }
                ]
            )
        if "/node/sync/blocks?from_height=1" in url:
            return FakeResponse(
                {
                    "from_height": 1,
                    "count": 1,
                    "blocks": [
                        {
                            "height": 2,
                            "previous_hash": snapshot["checkpoint"]["block_hash"],
                            "block_hash": "a" * 64,
                            "timestamp": "2026-05-12T00:01:00+00:00",
                        }
                    ],
                }
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr("app.services.network.requests.get", fake_get)
    result = reconcile_peer("http://peer-a:8000")

    assert result["sync_from_height"] == 1
    assert result["blocks_seen"] == 1
    assert result["blocks_imported"] == 1
    assert result["proposals_seen"] == 1
    assert result["proposals_imported"] == 0
    assert result["errors"] == []


def test_reconcile_after_restore_starts_after_snapshot_and_skips_stale_headers(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-restore-catchup-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-restore-catchup-miner", miner_key["public_key"])
    for _ in range(3):
        _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=3)
    stale_block = get_block(2)
    for _ in range(2):
        _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    new_blocks = get_blocks_since(3)["blocks"]

    _init_network_db(tmp_path, monkeypatch, "snapshot-restore-catchup-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO network_block_headers (
                block_hash, height, previous_hash, source_peer_id, status, reason, payload, received_at
            )
            VALUES (?, ?, ?, 'peer-a', 'pending_replay', 'stale pre-snapshot header', ?, ?)
            """,
            (
                stale_block["block_hash"],
                stale_block["height"],
                stale_block["previous_hash"],
                json.dumps(stale_block, sort_keys=True),
                "2026-05-10T00:00:00+00:00",
            ),
        )

    requested_urls: list[str] = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, timeout):
        requested_urls.append(url)
        if url.endswith("/node/identity"):
            return FakeResponse(
                {
                    "node_id": "peer-a",
                    "peer_address": "http://peer-a:8000",
                    "peer_type": "full",
                    "protocol_version": PROTOCOL_VERSION,
                    "network_id": NETWORK_ID,
                    "chain_id": CHAIN_ID,
                    "genesis_hash": GENESIS_HASH,
                    "bootstrap_peers": [],
                }
            )
        if url.endswith("/node/peers"):
            return FakeResponse([])
        if url.endswith("/mempool?limit=100"):
            return FakeResponse([])
        if url.endswith("/consensus/proposals?limit=100"):
            return FakeResponse([])
        if "/node/sync/blocks?from_height=3" in url:
            return FakeResponse({"from_height": 3, "count": len(new_blocks), "blocks": new_blocks})
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr("app.services.network.requests.get", fake_get)
    result = reconcile_peer("http://peer-a:8000")
    status = get_sync_status()
    chain = verify_chain()

    assert any("/node/sync/blocks?from_height=3" in url for url in requested_urls)
    assert not any("/node/sync/blocks?from_height=0" in url for url in requested_urls)
    assert result["local_block_height"] == 0
    assert result["snapshot_height"] == 3
    assert result["catch_up_start_height"] == 3
    assert result["blocks_seen"] == 2
    assert result["blocks_imported"] == 2
    assert result["replay"]["headers_imported"] == 2
    assert result["replay"]["headers_skipped_pre_snapshot"] == 1
    assert status["effective_latest_block_height"] == 5
    assert status["pending_replay_blocks"] == 0
    assert chain["valid"] is True


def test_replay_imports_pending_headers_after_active_snapshot_base(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-header-replay-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-header-replay-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    next_block = get_blocks_since(1)["blocks"][0]
    expected_height = next_block["height"]
    expected_hash = next_block["block_hash"]

    _init_network_db(tmp_path, monkeypatch, "snapshot-header-replay-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    received = receive_block_header(next_block, source_peer_id="peer-a")
    replay = replay_finalized_blocks()
    status = get_sync_status()
    audit = get_full_economic_audit()

    assert received["status"] == "pending_replay"
    assert replay["headers_imported"] == 1
    assert replay["errors"] == []
    assert status["latest_block_height"] == expected_height
    assert status["latest_block_hash"] == expected_hash
    assert status["effective_latest_block_height"] == expected_height
    assert audit["valid"] is True


def test_consensus_debug_block_reports_matching_canonical_hash(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "debug-block-determinism.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("debug-block-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    debug = debug_block_determinism(1)

    assert debug["expected_hash"] == get_block(1)["block_hash"]
    assert debug["matched"] is True
    assert debug["hash_input"] == canonical_json(debug["canonical_payload"])
    assert any(candidate["matches"] for candidate in debug["candidates"])


def test_replay_ignores_foreign_protocol_params_id_after_snapshot_base(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-header-foreign-protocol-source.sqlite3")
    with get_connection() as connection:
        connection.execute("UPDATE protocol_params SET id = 9999 WHERE active = 1")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-header-foreign-protocol-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    next_block = get_blocks_since(1)["blocks"][0]

    assert next_block["protocol_params_id"] == 9999

    _init_network_db(tmp_path, monkeypatch, "snapshot-header-foreign-protocol-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    received = receive_block_header(next_block, source_peer_id="peer-a")
    replay = replay_finalized_blocks()
    imported_block = get_block(next_block["height"])
    chain = verify_chain()

    assert received["status"] == "pending_replay"
    assert replay["headers_imported"] == 1
    assert replay["errors"] == []
    assert imported_block["block_hash"] == next_block["block_hash"]
    assert imported_block["protocol_params_id"] is None
    assert chain["valid"] is True


def test_health_reports_active_snapshot_tip_on_validator_restore(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-health-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-health-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)

    _init_network_db(tmp_path, monkeypatch, "snapshot-health-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    monkeypatch.setattr("app.services.mining.NODE_TYPE", "validator")

    health = get_health_status()

    assert health["status"] == "ok"
    assert health["latest_block_height"] == snapshot["checkpoint"]["height"]
    assert health["latest_block_hash"] == snapshot["checkpoint"]["block_hash"]
    assert health["local_block_height"] == 0
    assert health["local_block_hash"] == "0" * 64


def test_replay_imports_multiple_headers_after_snapshot_base(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-multi-header-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-multi-header-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    for _ in range(4):
        _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    blocks = get_blocks_since(1)["blocks"]

    _init_network_db(tmp_path, monkeypatch, "snapshot-multi-header-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    statuses = [receive_block_header(block, source_peer_id="peer-a")["status"] for block in blocks]
    pre_replay_status = get_sync_status()
    replay = replay_finalized_blocks()
    status = get_sync_status()
    audit = get_full_economic_audit()

    assert statuses[0] == "pending_replay"
    assert "pending_missing_ancestors" in statuses[1:]
    assert pre_replay_status["pending_replay_blocks"] == 4
    assert replay["headers_imported"] == 4
    assert replay["errors"] == []
    assert status["latest_block_height"] == 5
    assert status["pending_replay_blocks"] == 0
    assert audit["valid"] is True


def test_replay_enriches_pending_header_from_matching_proposal_payload(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-header-enrich-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-header-enrich-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    full_block = get_blocks_since(1)["blocks"][0]
    sparse_header = {key: full_block[key] for key in ("height", "previous_hash", "block_hash", "timestamp")}

    _init_network_db(tmp_path, monkeypatch, "snapshot-header-enrich-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    receive_block_header(sparse_header, source_peer_id="peer-a")
    propose_block(full_block, "peer-a", gossip=False)
    replay = replay_finalized_blocks()
    audit = get_full_economic_audit()

    assert replay["headers_imported"] == 1
    assert replay["errors"] == []
    assert get_block(full_block["height"])["block_hash"] == full_block["block_hash"]
    assert audit["valid"] is True


def test_header_replay_marks_matching_proposal_imported(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-header-import-status-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-header-import-status-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    full_block = get_blocks_since(1)["blocks"][0]

    _init_network_db(tmp_path, monkeypatch, "snapshot-header-import-status-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    receive_block_header(full_block, source_peer_id="peer-a")
    proposal = propose_block(full_block, "peer-a", gossip=False)
    replay = replay_finalized_blocks()
    status = get_sync_status()

    assert replay["headers_imported"] == 1
    assert status["consensus"].get("pending", 0) == 0
    with get_connection() as connection:
        row = connection.execute(
            "SELECT status FROM consensus_block_proposals WHERE proposal_id = ?",
            (proposal["proposal_id"],),
        ).fetchone()
    assert row["status"] == "imported"


def test_replay_normalizes_existing_block_proposals(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "existing-proposal-normalize.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("existing-proposal-normalize-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    block = get_block(1)
    proposal = propose_block(block, "peer-a", gossip=False)
    with get_connection() as connection:
        connection.execute(
            "UPDATE consensus_block_proposals SET status = 'pending' WHERE proposal_id = ?",
            (proposal["proposal_id"],),
        )

    replay = replay_finalized_blocks()
    status = get_sync_status()

    assert replay["normalized"] == 1
    assert status["consensus"].get("pending", 0) == 0
    with get_connection() as connection:
        row = connection.execute(
            "SELECT status FROM consensus_block_proposals WHERE proposal_id = ?",
            (proposal["proposal_id"],),
        ).fetchone()
    assert row["status"] == "imported"


def test_canonical_snapshot_import_rejects_tampered_balances(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-import-tamper.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-tamper-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    snapshot["balances"][0]["balance"] = round(float(snapshot["balances"][0]["balance"]) + 0.01, 8)
    validation = validate_snapshot_document(snapshot)

    assert validation["valid"] is False
    assert "balances_hash mismatch" in validation["issues"]


def test_science_stake_and_job_create_transactions_are_canonical(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "science-transactions.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("science-tx-miner", miner_key["public_key"])
    wallet = create_wallet("researcher")
    _fund_wallet_from_genesis(wallet["address"], 3_142.0)

    stake_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="stake",
        sender=wallet["address"],
        amount=3_141.6,
        nonce=1,
        fee=0.01,
    )
    submit_transaction(stake_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    account = get_science_account(wallet["address"])
    confirmed_stake = get_transaction(stake_tx["tx_hash"])

    assert confirmed_stake is not None
    assert confirmed_stake["status"] == "confirmed"
    assert account is not None
    assert account["tier"] == "researcher"
    assert account["stake_amount"] == pytest.approx(3_141.6)
    assert get_balance_amount(f"science_stake:{wallet['address']}") == pytest.approx(3_141.6)

    job_payload = {
        "job_type": "ai_inference",
        "metadata_hash": "metadata_hash",
        "storage_pointer": "ipfs://payload",
        "max_compute_units": 0,
        "reward_per_compute_unit": 0,
        "max_reward": 0,
    }
    job_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="science_job_create",
        sender=wallet["address"],
        amount=0,
        nonce=2,
        fee=0.01,
        payload=job_payload,
    )
    submit_transaction(job_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    expected_job_id = f"science_job_{job_tx['tx_hash'][:16]}"
    job = get_science_job(expected_job_id)
    confirmed_job = get_transaction(job_tx["tx_hash"])
    chain = verify_chain()

    assert confirmed_job is not None
    assert confirmed_job["status"] == "confirmed"
    assert job is not None
    assert job["requester_address"] == wallet["address"]
    assert job["job_type"] == "ai_inference"
    assert job["metadata_hash"] == "metadata_hash"
    assert job["storage_pointer"] == "ipfs://payload"
    assert get_balance_amount(wallet["address"]) == pytest.approx(0.38)
    assert get_balance_amount(miner["miner_id"]) == pytest.approx((2.104872 * 2) + 0.02)
    assert chain["valid"] is True


def test_science_unstake_transaction_unlocks_when_no_jobs_are_active(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "science-unstake-transaction.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("science-unstake-miner", miner_key["public_key"])
    wallet = create_wallet("researcher")
    _fund_wallet_from_genesis(wallet["address"], 3_142.0)

    stake_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="stake",
        sender=wallet["address"],
        amount=3_141.6,
        nonce=1,
        fee=0.01,
    )
    submit_transaction(stake_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    unstake_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="unstake",
        sender=wallet["address"],
        amount=0,
        nonce=2,
        fee=0.01,
    )
    submit_transaction(unstake_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    account = get_science_account(wallet["address"])
    confirmed_unstake = get_transaction(unstake_tx["tx_hash"])
    chain = verify_chain()

    assert confirmed_unstake is not None
    assert confirmed_unstake["status"] == "confirmed"
    assert account is not None
    assert account["status"] == "unstaking"
    assert account["stake_amount"] == pytest.approx(0)
    assert get_balance_amount(f"science_stake:{wallet['address']}") == pytest.approx(0)
    assert get_balance_amount(wallet["address"]) == pytest.approx(3_141.98)
    assert get_balance_amount(miner["miner_id"]) == pytest.approx((2.104872 * 2) + 0.02)
    assert chain["valid"] is True


def test_science_reserve_governance_actions_are_canonical_transactions(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "science-governance-transactions.sqlite3")
    monkeypatch.setattr("app.services.science.SCIENCE_RESERVE_GOVERNANCE_TIMELOCK_SECONDS", 0)

    miner_key = generate_keypair()
    miner = register_miner("science-governance-miner", miner_key["public_key"])
    signer_one = create_wallet("signer-one")
    signer_two = create_wallet("signer-two")
    _fund_wallet_from_genesis(signer_one["address"], 1.0)
    _fund_wallet_from_genesis(signer_two["address"], 1.0)
    _set_science_governance_signers([signer_one["address"], signer_two["address"]])

    propose_tx = _governance_tx(signer_one, "propose_activation", 1)
    submit_transaction(propose_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    proposed = get_science_reserve_governance()

    assert proposed["status"] == "L2_PENDING"
    assert signer_one["address"] in proposed["approvals"]

    approve_tx = _governance_tx(signer_two, "approve_activation", 1)
    submit_transaction(approve_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    approved = get_science_reserve_governance()

    assert signer_two["address"] in approved["approvals"]

    execute_tx = _governance_tx(signer_one, "execute_activation", 2)
    submit_transaction(execute_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    active = get_science_reserve_governance()

    assert active["status"] == "L2_ACTIVE"
    assert active["payouts_enabled"] is True

    pause_tx = _governance_tx(signer_two, "pause", 2)
    submit_transaction(pause_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    paused = get_science_reserve_governance()

    assert paused["status"] == "EMERGENCY_PAUSED"
    assert paused["emergency_paused"] is True

    unpause_tx = _governance_tx(signer_one, "unpause", 3)
    submit_transaction(unpause_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    unpaused = get_science_reserve_governance()
    chain = verify_chain()

    assert unpaused["status"] == "L2_ACTIVE"
    assert unpaused["payouts_enabled"] is True
    assert get_transaction(unpause_tx["tx_hash"])["status"] == "confirmed"
    assert get_balance_amount(miner["miner_id"]) == pytest.approx((2.104872 * 5) + 0.05)
    assert chain["valid"] is True


def test_scientific_development_treasury_claim_is_canonical_transaction(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "treasury-claim-transaction.sqlite3")
    monkeypatch.setattr("app.services.treasury.SCIENTIFIC_DEVELOPMENT_UNLOCK_INTERVAL_DAYS", 0)

    miner_key = generate_keypair()
    miner = register_miner("treasury-claim-miner", miner_key["public_key"])
    owner = create_wallet("owner")
    treasury_wallet = create_wallet("treasury")
    _fund_wallet_from_genesis(owner["address"], 1.0)
    monkeypatch.setattr("app.services.treasury.SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET", owner["address"])
    monkeypatch.setattr("app.services.treasury.SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET", treasury_wallet["address"])

    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    unlocked = get_scientific_development_treasury()
    assert unlocked["unlocked_balance"] == pytest.approx(0.094248)

    claim_tx = sign_transaction(
        private_key=owner["private_key"],
        public_key=owner["public_key"],
        tx_type="treasury_claim",
        sender=owner["address"],
        amount=0,
        nonce=1,
        fee=0.01,
        payload={"claim_to": treasury_wallet["address"], "claim_id": "owner_claim_round_1"},
    )
    submit_transaction(claim_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    claimed = get_scientific_development_treasury()
    confirmed = get_transaction(claim_tx["tx_hash"])
    chain = verify_chain()

    assert confirmed is not None
    assert confirmed["status"] == "confirmed"
    assert claimed["total_claimed"] == pytest.approx(0.094248)
    assert claimed["unlocked_balance"] == pytest.approx(0.094248)
    assert get_balance_amount(treasury_wallet["address"]) == pytest.approx(0.094248)
    assert get_balance_amount(SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID) == pytest.approx(0.094248)
    assert get_balance_amount(owner["address"]) == pytest.approx(0.99)
    assert get_balance_amount(miner["miner_id"]) == pytest.approx((2.104872 * 2) + 0.01)
    assert chain["valid"] is True

    duplicate = sign_transaction(
        private_key=owner["private_key"],
        public_key=owner["public_key"],
        tx_type="treasury_claim",
        sender=owner["address"],
        amount=0,
        nonce=2,
        fee=0.01,
        payload={"claim_to": treasury_wallet["address"], "claim_id": "owner_claim_round_1"},
    )
    submit_transaction(duplicate)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    rejected = get_transaction(duplicate["tx_hash"])

    assert rejected is not None
    assert rejected["status"] == "rejected"
    assert "already exists" in rejected["rejection_reason"]


def _fund_wallet_from_genesis(address: str, amount: float) -> None:
    timestamp = "2026-05-12T00:00:00+00:00"
    with get_connection() as connection:
        genesis = connection.execute("SELECT balance FROM balances WHERE account_id = 'genesis'").fetchone()
        genesis_after = round(float(genesis["balance"]) - amount, 8)
        connection.execute(
            "UPDATE balances SET balance = ?, updated_at = ? WHERE account_id = 'genesis'",
            (genesis_after, timestamp),
        )
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, balance_after, entry_type,
                block_height, related_id, description, created_at
            )
            VALUES ('genesis', 'genesis', ?, ?, 'test_wallet_funding', NULL, ?, 'test wallet funding debit', ?)
            """,
            (-amount, genesis_after, address, timestamp),
        )
        connection.execute(
            """
            INSERT INTO balances (account_id, account_type, balance, updated_at)
            VALUES (?, 'wallet', ?, ?)
            """,
            (address, amount, timestamp),
        )
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, balance_after, entry_type,
                block_height, related_id, description, created_at
            )
            VALUES (?, 'wallet', ?, ?, 'test_wallet_funding', NULL, 'genesis', 'test wallet funding credit', ?)
            """,
            (address, amount, amount, timestamp),
        )


def _set_science_governance_signers(signers: list[str]) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE science_reserve_governance SET authorized_signers = ?, updated_at = ? WHERE id = 1",
            (json.dumps(signers), "2026-05-12T00:00:00+00:00"),
        )


def _governance_tx(wallet: dict, action: str, nonce: int) -> dict:
    return sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="governance_action",
        sender=wallet["address"],
        amount=0,
        nonce=nonce,
        fee=0.01,
        payload={"scope": "science_reserve", "action": action},
    )


def _mine_legacy_block(miner_id: str, private_key: str) -> None:
    task = create_next_task(miner_id)
    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    signed_at = "2026-05-12T00:00:00+00:00"
    payload = build_submission_signature_payload(
        task_id=task["task_id"],
        miner_id=miner_id,
        range_start=task["range_start"],
        range_end=task["range_end"],
        algorithm=task["algorithm"],
        result_hash=result_hash,
        signed_at=signed_at,
    )
    signature = sign_payload(private_key, payload)
    response = submit_task(task["task_id"], miner_id, result_hash, segment, signature, signed_at)
    assert response["accepted"] is True
