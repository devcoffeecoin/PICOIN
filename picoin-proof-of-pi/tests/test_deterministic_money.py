from app.core.money import canonical_amount, to_units
from app.core.signatures import generate_keypair
from app.db.database import get_connection, init_db
from app.services.state import balance_snapshot, calculate_state_root
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
