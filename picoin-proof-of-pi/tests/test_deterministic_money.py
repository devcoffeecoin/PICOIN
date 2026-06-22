import pytest

from app.core.settings import VALIDATOR_REGISTRATION_STAKE
from app.core.money import canonical_amount, to_units
from app.core.signatures import generate_keypair
from app.db.database import get_connection, init_db
from app.services.state import (
    balance_snapshot,
    calculate_state_root,
    export_canonical_snapshot,
    import_canonical_snapshot,
    restore_imported_snapshot_state,
)
from app.services.transactions import TransactionExecutionError, apply_block_transactions, get_wallet_nonce_status
from app.services.wallet import address_from_public_key, sign_transaction


def _init_db(tmp_path, monkeypatch, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)


def test_money_units_are_canonical() -> None:
    assert to_units("1") == 1_000_000
    assert to_units("1.234567") == 1_234_567
    assert to_units("0.000001") == 1
    assert canonical_amount(1_234_567) == "1.234567"


def test_state_root_uses_canonical_units_not_float_payload(tmp_path, monkeypatch) -> None:
    _init_db(tmp_path, monkeypatch, "deterministic-state.sqlite3")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, amount_units, balance_after, balance_after_units,
                entry_type, block_height, related_id, description, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "PIABC",
                "wallet",
                1.234567,
                1_234_567,
                1.234567,
                1_234_567,
                "test_credit",
                1,
                "test",
                "test",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        snapshot = balance_snapshot(connection, 1, "2026-01-01T00:00:00+00:00")
        first_root = calculate_state_root(connection, 1, "2026-01-01T00:00:00+00:00")
        second_root = calculate_state_root(connection, 1, "2026-01-01T00:00:00+00:00")

    wallet_balance = next(item for item in snapshot if item["account_id"] == "PIABC")
    assert wallet_balance == {
        "account_id": "PIABC",
        "account_type": "wallet",
        "balance": "1.234567",
        "balance_units": 1_234_567,
    }
    assert all(isinstance(item["balance"], str) for item in snapshot)
    assert first_root == second_root


def test_wallet_signed_transaction_payload_uses_units() -> None:
    keypair = generate_keypair()
    sender = address_from_public_key(keypair["public_key"])

    tx = sign_transaction(
        private_key=keypair["private_key"],
        public_key=keypair["public_key"],
        tx_type="transfer",
        sender=sender,
        recipient=sender,
        amount=1.25,
        fee=0.001,
        nonce=1,
        timestamp="2026-01-01T00:00:00+00:00",
    )

    assert tx["amount"] == "1.250000"
    assert tx["amount_units"] == 1_250_000
    assert tx["fee"] == "0.001000"
    assert tx["fee_units"] == 1_000


def test_canonical_replay_creates_missing_validator_registration_for_stake(tmp_path, monkeypatch) -> None:
    _init_db(tmp_path, monkeypatch, "canonical-replay-validator-stake.sqlite3")
    keypair = generate_keypair()
    sender = address_from_public_key(keypair["public_key"])
    validator_id = "validator_missingabc123"
    timestamp = "2026-01-01T00:00:00+00:00"
    amount = 31.416
    fee = 0.001

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO balances (account_id, account_type, balance, balance_units, updated_at)
            VALUES (?, 'wallet', 40.0, 40000000, ?)
            """,
            (sender, timestamp),
        )

    tx = sign_transaction(
        private_key=keypair["private_key"],
        public_key=keypair["public_key"],
        tx_type="stake",
        sender=sender,
        amount=amount,
        fee=fee,
        nonce=1,
        payload={"stake_type": "validator", "validator_id": validator_id},
        timestamp=timestamp,
    )

    with get_connection() as connection:
        with pytest.raises(TransactionExecutionError, match="registered validator"):
            apply_block_transactions(
                connection,
                miner_id="miner_test",
                block_height=2,
                transactions=[tx],
                timestamp=timestamp,
            )

    with get_connection() as connection:
        result = apply_block_transactions(
            connection,
            miner_id="miner_test",
            block_height=2,
            transactions=[tx],
            timestamp=timestamp,
            canonical_replay=True,
        )
        validator = connection.execute(
            """
            SELECT name, public_key, stake_locked, wallet_stake_locked, stake_owner_address,
                   online_status, sync_status
            FROM validators
            WHERE validator_id = ?
            """,
            (validator_id,),
        ).fetchone()
        sender_balance = connection.execute(
            "SELECT balance_units FROM balances WHERE account_id = ?",
            (sender,),
        ).fetchone()
        validator_balance = connection.execute(
            "SELECT balance_units FROM balances WHERE account_id = ?",
            (validator_id,),
        ).fetchone()

    assert result["applied"] == [tx["tx_hash"]]
    assert validator is not None
    assert validator["name"] == f"canonical-replay:{validator_id}"
    assert validator["public_key"] == f"ed25519:canonical-replay:{validator_id}"
    assert validator["stake_locked"] == pytest.approx(VALIDATOR_REGISTRATION_STAKE + amount)
    assert validator["wallet_stake_locked"] == pytest.approx(amount)
    assert validator["stake_owner_address"] == sender
    assert validator["online_status"] == "offline"
    assert validator["sync_status"] == "unknown"
    assert sender_balance["balance_units"] == 40_000_000 - to_units(amount) - to_units(fee)
    assert validator_balance["balance_units"] == to_units(amount)


def test_snapshot_restore_preserves_wallet_nonce_for_replay(tmp_path, monkeypatch) -> None:
    _init_db(tmp_path, monkeypatch, "source-snapshot-nonce.sqlite3")
    keypair = generate_keypair()
    sender = address_from_public_key(keypair["public_key"])
    recipient_keypair = generate_keypair()
    recipient = address_from_public_key(recipient_keypair["public_key"])
    timestamp = "2026-01-01T00:00:00+00:00"

    with get_connection() as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, amount_units, balance_after, balance_after_units,
                entry_type, block_height, related_id, description, created_at
            )
            VALUES (?, 'wallet', 10.0, 10000000, 10.0, 10000000, 'test_credit', 1, 'seed', 'seed', ?)
            """,
            (sender, timestamp),
        )
        connection.execute(
            """
            INSERT INTO account_nonces (account_id, nonce, updated_at)
            VALUES (?, 7, ?)
            """,
            (sender, timestamp),
        )
        connection.execute(
            """
            INSERT INTO blocks (
                height, previous_hash, miner_id, range_start, range_end, algorithm,
                result_hash, merkle_root, samples, timestamp, block_hash, reward,
                reward_units, task_id
            )
            VALUES (1, 'genesis', 'miner_test', 1, 1, 'bbp_hex_v1', 'result_1', '',
                    '[]', ?, 'block_1', 0, 0, 'task_1')
            """,
            (timestamp,),
        )

    snapshot = export_canonical_snapshot(1)
    assert snapshot["nonces"] == [{"account_id": sender, "nonce": 7}]

    _init_db(tmp_path, monkeypatch, "target-snapshot-nonce.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="test")
    restore_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])

    with get_connection() as connection:
        nonce_status = get_wallet_nonce_status(connection, sender)
    assert nonce_status["confirmed_nonce"] == 7
    assert nonce_status["next_nonce"] == 8

    tx = sign_transaction(
        private_key=keypair["private_key"],
        public_key=keypair["public_key"],
        tx_type="transfer",
        sender=sender,
        recipient=recipient,
        amount=1,
        fee=0.001,
        nonce=8,
        timestamp="2026-01-01T00:01:00+00:00",
    )
    with get_connection() as connection:
        result = apply_block_transactions(
            connection,
            miner_id="miner_test",
            block_height=2,
            transactions=[tx],
            timestamp="2026-01-01T00:01:01+00:00",
        )

    assert result["applied"] == [tx["tx_hash"]]
