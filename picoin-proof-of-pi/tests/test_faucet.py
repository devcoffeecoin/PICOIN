import pytest

from app.core.settings import FAUCET_MAX_AMOUNT, GENESIS_SUPPLY
from app.core.signatures import generate_keypair
from app.db.database import init_db
from app.services.mining import MiningError, get_balance, get_ledger_entries, register_miner, request_faucet


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
