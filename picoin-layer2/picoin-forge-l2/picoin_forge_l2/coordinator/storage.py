from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from picoin_forge_l2.common.hashing import hash_json
from picoin_forge_l2.common.models import CoordinatorEvent, utc_now


class CoordinatorStorage:
    """Small SQLite store for the L2 MVP.

    The store is intentionally local-only. It does not touch Picoin L1.
    """

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "coordinator.sqlite3"
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def init_db(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS challenges (
                    challenge_id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_challenges_worker_id
                    ON challenges(worker_id);

                CREATE TABLE IF NOT EXISTS epoch_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    current_epoch INTEGER NOT NULL
                );

                INSERT OR IGNORE INTO epoch_state (id, current_epoch)
                VALUES (1, 0);

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    subject_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_type_created
                    ON events(event_type, created_at);

                CREATE INDEX IF NOT EXISTS idx_events_subject_created
                    ON events(subject_id, created_at);
                """
            )

    def record_event(self, event_type: str, subject_id: str | None = None, payload: dict[str, Any] | None = None) -> CoordinatorEvent:
        event_payload = payload or {}
        created_at = utc_now()
        event_id = "event_" + hash_json(
            {
                "event_type": event_type,
                "subject_id": subject_id,
                "payload": event_payload,
                "created_at": created_at.isoformat(),
            }
        )[:20]
        event = CoordinatorEvent(
            event_id=event_id,
            event_type=event_type,
            subject_id=subject_id,
            payload=event_payload,
            created_at=created_at,
        )
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO events (event_id, event_type, subject_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.subject_id,
                    event.model_dump_json(),
                    event.created_at.isoformat(),
                ),
            )
        return event

    def list_events(self, limit: int = 100) -> list[CoordinatorEvent]:
        safe_limit = max(1, min(int(limit), 1000))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM events ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [CoordinatorEvent.model_validate_json(row["payload"]) for row in rows]
