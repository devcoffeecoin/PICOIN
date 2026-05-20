import pytest

from app.core.settings import FAUCET_MAX_AMOUNT, GENESIS_SUPPLY
from app.core.signatures import generate_keypair
from app.db.database import get_connection, init_db
from app.services.mining import MiningError, get_balance, get_ledger_entries, register_miner, request_faucet
from app.services.network import submit_transaction
from app.services.transactions import apply_block_transactions
from app.services.wallet import create_wallet, legacy_address_from_public_key, sign_transaction


def test_local_faucet_credits_registered_miner_from_genesis_reward_supply(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "faucet.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    keypair = generate_keypair()
    miner = register_miner("faucet-miner", keypair["public_key"])

    result = request_faucet(miner["miner_id"], "miner", 1.5)
    miner_balance = get_balance(miner["miner_id"])
    genesis_balance = get_balance("genesis")
    miner_ledger = get_ledger_entries(miner["miner_id"])

    assert result["amount"] == 1.5
    assert result["balance"] == 1.5
    assert miner_balance["balance"] == 1.5
    assert genesis_balance["balance"] == GENESIS_SUPPLY - 1.5
    assert miner_ledger[0]["entry_type"] == "faucet_credit"


def test_local_faucet_rejects_unknown_account_and_large_amount(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "faucet-reject.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    with pytest.raises(MiningError):
        request_faucet("miner_missing", "miner", 1.0)

    keypair = generate_keypair()
    miner = register_miner("faucet-limit", keypair["public_key"])
    with pytest.raises(MiningError):
        request_faucet(miner["miner_id"], "miner", FAUCET_MAX_AMOUNT + 0.1)


def test_local_faucet_can_fund_wallet_account(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "wallet-faucet.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    wallet_address = "PI_TEST_WALLET"
    result = request_faucet(wallet_address, "wallet", 0.25)
    wallet_balance = get_balance(wallet_address)
    wallet_ledger = get_ledger_entries(wallet_address)

    assert result["amount"] == 0.25
    assert result["account_type"] == "wallet"
    assert wallet_balance["account_type"] == "wallet"
    assert wallet_balance["balance"] == 0.25
    assert wallet_ledger[0]["entry_type"] == "faucet_credit"


def test_signed_faucet_transaction_replays_as_canonical_block_state(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "signed-wallet-faucet.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    wallet = create_wallet("signed-faucet-wallet")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="faucet",
        sender=wallet["address"],
        amount=0.25,
        nonce=1,
        fee=0.0,
    )

    submitted = submit_transaction(tx)
    with get_connection() as connection:
        execution = apply_block_transactions(
            connection,
            miner_id="miner_test",
            block_height=1,
            transactions=[submitted],
            timestamp="2026-05-17T18:30:00+00:00",
        )

    wallet_balance = get_balance(wallet["address"])
    genesis_balance = get_balance("genesis")

    assert execution["applied"] == [tx["tx_hash"]]
    assert wallet_balance["account_type"] == "wallet"
    assert wallet_balance["balance"] == 0.25
    assert genesis_balance["balance"] == pytest.approx(GENESIS_SUPPLY - 0.25)


def test_signed_faucet_accepts_legacy_sender_address_for_existing_wallets(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "legacy-signed-wallet-faucet.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    wallet = create_wallet("legacy-signed-faucet-wallet")
    legacy_address = legacy_address_from_public_key(wallet["public_key"])
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="faucet",
        sender=legacy_address,
        amount=0.25,
        nonce=1,
        fee=0.0,
    )

    submitted = submit_transaction(tx)

    assert submitted["sender"] == legacy_address
    assert submitted["status"] == "pending"


def test_signed_faucet_can_retry_nonce_after_rejected_mempool_entry(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "retry-rejected-wallet-faucet.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    wallet = create_wallet("retry-faucet-wallet")
    first_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="faucet",
        sender=wallet["address"],
        amount=3.1416,
        nonce=1,
        fee=0.0,
    )
    submit_transaction(first_tx)
    with get_connection() as connection:
        connection.execute(
            "UPDATE mempool_transactions SET status = 'rejected', rejection_reason = 'test retry' WHERE tx_hash = ?",
            (first_tx["tx_hash"],),
        )

    retry_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="faucet",
        sender=wallet["address"],
        amount=1.0,
        nonce=1,
        fee=0.0,
    )
    submitted = submit_transaction(retry_tx)

    assert submitted["tx_hash"] == retry_tx["tx_hash"]
    assert submitted["status"] == "pending"
