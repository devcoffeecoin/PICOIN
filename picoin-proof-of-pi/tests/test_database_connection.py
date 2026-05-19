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
