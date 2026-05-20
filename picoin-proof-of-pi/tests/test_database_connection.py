import sqlite3

import pytest

from app.db.database import get_connection, init_db


def test_get_connection_context_manager_closes_physical_connection(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "connection-close.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    with get_connection() as connection:
        connection.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1").fetchone()


def test_money_unit_backfill_is_idempotent_and_skips_malformed_rows(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "money-backfill.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO balances (account_id, account_type, balance, balance_units, updated_at)
            VALUES ('PI_VALID_BACKFILL', 'wallet', 1.234567, 0, '2026-01-01T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO balances (account_id, account_type, balance, balance_units, updated_at)
            VALUES ('PI_MALFORMED_BACKFILL', 'wallet', 'not-a-number', 0, '2026-01-01T00:00:00+00:00')
            """
        )

    init_db(db_path)
    init_db(db_path)

    with sqlite3.connect(db_path) as connection:
        valid_units = connection.execute(
            "SELECT balance_units FROM balances WHERE account_id = 'PI_VALID_BACKFILL'"
        ).fetchone()[0]
        malformed_units = connection.execute(
            "SELECT balance_units FROM balances WHERE account_id = 'PI_MALFORMED_BACKFILL'"
        ).fetchone()[0]

    assert valid_units == 1_234_567
    assert malformed_units == 0
