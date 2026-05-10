import sqlite3
from pathlib import Path
from typing import Any

from app.core.settings import DATA_DIR, DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def init_db(db_path: Path = DATABASE_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS miners (
                miner_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                public_key TEXT,
                registered_at TEXT NOT NULL,
                trust_score REAL NOT NULL DEFAULT 1.0,
                cooldown_until TEXT,
                is_banned INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                miner_id TEXT NOT NULL,
                range_start INTEGER NOT NULL,
                range_end INTEGER NOT NULL,
                algorithm TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                submitted_at TEXT,
                UNIQUE(range_start, range_end, algorithm),
                FOREIGN KEY(miner_id) REFERENCES miners(miner_id)
            );

            CREATE TABLE IF NOT EXISTS blocks (
                height INTEGER PRIMARY KEY,
                previous_hash TEXT NOT NULL,
                miner_id TEXT NOT NULL,
                range_start INTEGER NOT NULL,
                range_end INTEGER NOT NULL,
                algorithm TEXT NOT NULL,
                result_hash TEXT NOT NULL UNIQUE,
                samples TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                block_hash TEXT NOT NULL UNIQUE,
                reward REAL NOT NULL,
                task_id TEXT NOT NULL UNIQUE,
                protocol_version TEXT NOT NULL DEFAULT '0.2',
                validation_mode TEXT NOT NULL DEFAULT 'full',
                FOREIGN KEY(miner_id) REFERENCES miners(miner_id),
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
            );

            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                miner_id TEXT NOT NULL,
                result_hash TEXT NOT NULL,
                segment_length INTEGER NOT NULL,
                signature TEXT,
                accepted INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(miner_id) REFERENCES miners(miner_id)
            );

            CREATE TABLE IF NOT EXISTS rewards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                miner_id TEXT NOT NULL,
                block_height INTEGER NOT NULL,
                amount REAL NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(miner_id) REFERENCES miners(miner_id),
                FOREIGN KEY(block_height) REFERENCES blocks(height)
            );

            CREATE TABLE IF NOT EXISTS penalties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                miner_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                points INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(miner_id) REFERENCES miners(miner_id)
            );

            CREATE TABLE IF NOT EXISTS rejected_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                miner_id TEXT NOT NULL,
                result_hash TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_blocks_miner ON blocks(miner_id);
            CREATE INDEX IF NOT EXISTS idx_submissions_miner ON submissions(miner_id);
            CREATE INDEX IF NOT EXISTS idx_penalties_miner ON penalties(miner_id);
            """
        )
        _ensure_column(connection, "miners", "trust_score", "REAL NOT NULL DEFAULT 1.0")
        _ensure_column(connection, "miners", "cooldown_until", "TEXT")
        _ensure_column(connection, "miners", "is_banned", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "tasks", "expires_at", "TEXT")
        _ensure_column(connection, "blocks", "protocol_version", "TEXT NOT NULL DEFAULT '0.2'")
        _ensure_column(connection, "blocks", "validation_mode", "TEXT NOT NULL DEFAULT 'full'")


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
