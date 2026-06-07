from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from pool_accounting import (
    WorkChunk,
    assemble_segment,
    filter_shares_by_window,
    split_range,
    split_range_balanced,
    summarize_round_window_payouts,
    summarize_shares,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = Path(os.getenv("PICOIN_CORE_PATH", REPO_ROOT / "picoin-proof-of-pi"))
if str(CORE_PATH) not in sys.path:
    sys.path.insert(0, str(CORE_PATH))

from app.core.crypto import hash_result  # noqa: E402
from app.core.merkle import merkle_root  # noqa: E402
from app.core.money import to_units, units_to_float  # noqa: E402
from app.core.performance import elapsed_ms, now_perf  # noqa: E402
from app.core.pi import calculate_pi_segment  # noqa: E402
from app.services.wallet import address_from_public_key, address_matches_public_key, is_valid_address, sign_transaction  # noqa: E402
from miner.client import (  # noqa: E402
    TaskUnavailable,
    commit_result,
    get_task_for_identity,
    load_or_register_identity,
    reveal_samples,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def is_lost_competitive_round_error(message: str) -> bool:
    return message.startswith("commit rejected: competitive round won by ")


def parse_iso_timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def floor_picoin_units(amount: Any) -> int:
    value = Decimal(str(amount or "0")).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    return int((value * Decimal("1000000")).to_integral_value(rounding=ROUND_DOWN))


def load_payout_wallet(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    if not path.exists():
        raise FileNotFoundError(f"payout wallet not found: {path}")
    wallet = json.loads(path.read_text(encoding="utf-8"))
    private_key = wallet.get("private_key") or wallet.get("privateKey")
    public_key = wallet.get("public_key") or wallet.get("publicKey")
    address = str(wallet.get("address") or "").strip().upper()
    if not public_key or not private_key:
        raise ValueError("payout wallet must include public_key and private_key")
    if not address:
        address = address_from_public_key(public_key)
    if not is_valid_address(address):
        raise ValueError("payout wallet address must be a valid PI wallet address")
    if not address_matches_public_key(address, public_key):
        raise ValueError("payout wallet address does not match public_key")
    return {
        "address": address,
        "public_key": public_key,
        "private_key": private_key,
    }


class PoolDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.init()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=60)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=60000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def init(self) -> None:
        with self._lock, self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pool_workers (
                    worker_id TEXT PRIMARY KEY,
                    name TEXT,
                    payout_address TEXT,
                    registered_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pool_tasks (
                    pool_task_id TEXT PRIMARY KEY,
                    mainnet_task_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    range_start INTEGER NOT NULL,
                    range_end INTEGER NOT NULL,
                    algorithm TEXT NOT NULL,
                    raw_task_json TEXT NOT NULL,
                    result_hash TEXT,
                    merkle_root TEXT,
                    compute_ms INTEGER DEFAULT 0,
                    raw_commit_json TEXT,
                    raw_reveal_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS pool_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    pool_task_id TEXT NOT NULL REFERENCES pool_tasks(pool_task_id) ON DELETE CASCADE,
                    worker_id TEXT REFERENCES pool_workers(worker_id),
                    status TEXT NOT NULL,
                    range_start INTEGER NOT NULL,
                    range_end INTEGER NOT NULL,
                    segment TEXT,
                    compute_ms INTEGER DEFAULT 0,
                    assigned_at TEXT,
                    submitted_at TEXT,
                    units INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pool_shares (
                    share_id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    pool_task_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    units INTEGER NOT NULL,
                    credited INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pool_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT
                );

                CREATE TABLE IF NOT EXISTS pool_payouts (
                    payout_id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    payout_address TEXT NOT NULL,
                    amount REAL NOT NULL,
                    amount_units INTEGER NOT NULL,
                    fee REAL NOT NULL DEFAULT 0,
                    fee_units INTEGER NOT NULL DEFAULT 0,
                    tx_hash TEXT UNIQUE,
                    status TEXT NOT NULL,
                    error TEXT,
                    raw_tx_json TEXT,
                    raw_response_json TEXT,
                    created_at TEXT NOT NULL,
                    submitted_at TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def event(self, level: str, message: str, payload: dict[str, Any] | None = None) -> None:
        try:
            with self._lock, self.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO pool_events (created_at, level, message, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (utc_now(), level, message, json_dumps(payload or {})),
                )
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            print(
                json_dumps(
                    {
                        "created_at": utc_now(),
                        "level": level,
                        "message": message,
                        "payload": payload or {},
                        "warning": "pool event skipped because sqlite database was locked",
                    }
                )
            )


class PoolCoordinator:
    def __init__(
        self,
        *,
        db: PoolDatabase,
        server_url: str,
        identity: dict[str, Any],
        chunk_size: int | str | None,
        poll_seconds: float,
        chunk_timeout_seconds: int,
        verify_chunks: bool,
        require_worker_payout: bool,
        pool_fee_percent: float,
        speculative_chunks: bool = True,
        payout_wallet: dict[str, Any] | None = None,
        payout_interval_seconds: int = 7200,
        payout_min_amount: float = 0.1,
        payout_fee: float = 0.0,
        payout_confirmation_grace_seconds: int = 600,
        settlement_block_limit: int = 100,
    ) -> None:
        self.db = db
        self.server_url = server_url.rstrip("/")
        self.identity = identity
        normalized_chunk_size = self._normalize_chunk_size(chunk_size)
        self.hybrid_race = normalized_chunk_size == "hybrid-race"
        self.chunk_size = normalized_chunk_size if isinstance(normalized_chunk_size, int) else None
        self.poll_seconds = max(0.5, poll_seconds)
        self.chunk_timeout_seconds = max(5, chunk_timeout_seconds)
        self.verify_chunks = verify_chunks
        self.require_worker_payout = require_worker_payout
        self.pool_fee_percent = max(0.0, min(100.0, float(pool_fee_percent)))
        self.speculative_chunks = bool(speculative_chunks)
        self.payout_wallet = payout_wallet
        self.payout_interval_seconds = max(1, int(payout_interval_seconds))
        self.payout_min_amount = max(0.0, float(payout_min_amount))
        self.payout_fee = max(0.0, float(payout_fee))
        self.payout_confirmation_grace_seconds = max(1, int(payout_confirmation_grace_seconds))
        self.settlement_block_limit = max(1, min(500, int(settlement_block_limit)))
        self._last_payout_attempt = 0.0
        self.stop_event = threading.Event()

    @staticmethod
    def _normalize_chunk_size(chunk_size: int | str | None) -> int | str | None:
        if chunk_size is None:
            return None
        if isinstance(chunk_size, str):
            value = chunk_size.strip().lower()
            if value in {"", "auto", "0"}:
                return None
            if value in {"hybrid", "hybrid-race", "hybrid_race"}:
                return "hybrid-race"
            chunk_size = int(value)
        return max(1, int(chunk_size))

    def _configured_chunk_mode(self) -> str:
        if self.chunk_size:
            return "fixed"
        if self.hybrid_race:
            return "hybrid-race"
        return "auto"

    def _configured_chunk_strategy(self) -> str:
        if self.chunk_size:
            return "fixed"
        if self.hybrid_race:
            return "hybrid_race"
        return "one_unit_speculative_queue"

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="picoin-pool-coordinator", daemon=True)
        thread.start()
        return thread

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:  # pragma: no cover - safety net for long-running process
                self.db.event("error", "coordinator tick failed", {"error": str(exc)})
            self.stop_event.wait(self.poll_seconds)

    def tick(self) -> None:
        self.expire_stale_assignments()
        self.finalize_ready_tasks()
        self.reconcile_won_blocks()
        self.reconcile_mainnet_task_statuses()
        self.ensure_active_task()
        self.reconcile_payout_statuses()
        self.maybe_run_payouts()

    def register_worker(self, worker_id: str, name: str | None, payout_address: str | None) -> dict[str, Any]:
        worker_id = worker_id.strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        normalized_payout = self._normalize_payout_address(payout_address)
        if self.require_worker_payout and not normalized_payout:
            raise ValueError("payout_address is required for public pool workers")
        now = utc_now()
        with self.db._lock, self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    name = COALESCE(excluded.name, pool_workers.name),
                    payout_address = COALESCE(pool_workers.payout_address, excluded.payout_address),
                    last_seen_at = excluded.last_seen_at
                """,
                (worker_id, name, normalized_payout, now, now),
            )
        return {"status": "ok", "worker_id": worker_id, "checked_at": utc_now()}

    def claim_work(self, worker_id: str) -> dict[str, Any]:
        worker_id = worker_id.strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        with self.db._lock, self.db.connect() as connection:
            worker = connection.execute(
                "SELECT worker_id, payout_address FROM pool_workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
            now = utc_now()
            if worker is None:
                if self.require_worker_payout:
                    raise ValueError("worker must register with payout_address before requesting work")
                connection.execute(
                    """
                    INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
                    VALUES (?, NULL, NULL, ?, ?)
                    """,
                    (worker_id, now, now),
                )
            elif self.require_worker_payout and not worker["payout_address"]:
                raise ValueError("worker payout_address is required before requesting work")

            connection.execute(
                "UPDATE pool_workers SET last_seen_at = ? WHERE worker_id = ?",
                (now, worker_id),
            )
            row = connection.execute(
                """
                SELECT c.*, t.mainnet_task_id, t.algorithm
                FROM pool_chunks c
                JOIN pool_tasks t ON t.pool_task_id = c.pool_task_id
                WHERE c.status = 'pending'
                  AND t.status IN ('gathering', 'active')
                ORDER BY c.range_start ASC
                LIMIT 1
                """
            ).fetchone()
            assignment_mode = "primary"
            if row is None and self.speculative_chunks:
                row = connection.execute(
                    """
                    SELECT c.*, t.mainnet_task_id, t.algorithm
                    FROM pool_chunks c
                    JOIN pool_tasks t ON t.pool_task_id = c.pool_task_id
                    WHERE c.status = 'assigned'
                      AND t.status IN ('gathering', 'active')
                    ORDER BY CASE WHEN c.worker_id = ? THEN 1 ELSE 0 END,
                             COALESCE(c.assigned_at, '') ASC,
                             RANDOM()
                    LIMIT 1
                    """,
                    (worker_id,),
                ).fetchone()
                if row is not None:
                    assignment_mode = "speculative"
            if row is None:
                return {"status": "idle", "message": "no pool work available", "checked_at": now}
            if assignment_mode == "primary":
                connection.execute(
                    """
                    UPDATE pool_chunks
                    SET status = 'assigned', worker_id = ?, assigned_at = ?
                    WHERE chunk_id = ? AND status = 'pending'
                    """,
                    (worker_id, now, row["chunk_id"]),
                )
            else:
                connection.execute(
                    """
                    UPDATE pool_chunks
                    SET worker_id = ?, assigned_at = ?
                    WHERE chunk_id = ? AND status = 'assigned'
                    """,
                    (worker_id, now, row["chunk_id"]),
                )
            connection.execute(
                """
                INSERT INTO pool_events (created_at, level, message, payload_json)
                VALUES (?, 'info', 'pool chunk assigned', ?)
                """,
                (
                    now,
                    json_dumps(
                        {
                            "assignment_mode": assignment_mode,
                            "chunk_id": row["chunk_id"],
                            "mainnet_task_id": row["mainnet_task_id"],
                            "pool_task_id": row["pool_task_id"],
                            "previous_assigned_at": row["assigned_at"],
                            "previous_worker_id": row["worker_id"],
                            "range_end": row["range_end"],
                            "range_start": row["range_start"],
                            "units": row["units"],
                            "worker_id": worker_id,
                        }
                    ),
                ),
            )
            connection.execute(
                "UPDATE pool_workers SET last_seen_at = ? WHERE worker_id = ?",
                (now, worker_id),
            )
            return {
                "status": "work",
                "chunk_id": row["chunk_id"],
                "pool_task_id": row["pool_task_id"],
                "task_id": row["mainnet_task_id"],
                "range_start": row["range_start"],
                "range_end": row["range_end"],
                "algorithm": row["algorithm"],
                "assignment_mode": assignment_mode,
                "checked_at": now,
            }

    def submit_work(self, worker_id: str, chunk_id: str, segment: str, compute_ms: int) -> dict[str, Any]:
        worker_id = worker_id.strip()
        chunk_id = chunk_id.strip()
        segment = segment.strip().upper()
        with self.db._lock, self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT c.*, t.mainnet_task_id, t.algorithm, t.status AS task_status,
                       t.range_start AS task_range_start,
                       t.range_end AS task_range_end
                FROM pool_chunks c
                JOIN pool_tasks t ON t.pool_task_id = c.pool_task_id
                WHERE c.chunk_id = ?
                """,
                (chunk_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown chunk_id")
            row_data = dict(row)

        expected_len = int(row_data["range_end"]) - int(row_data["range_start"]) + 1
        if len(segment) != expected_len:
            raise ValueError("segment length does not match chunk range")
        if row_data["status"] == "completed":
            full_race_result = self._credit_late_full_task_race_share(row_data, worker_id, compute_ms)
            if full_race_result is not None:
                return full_race_result
            return {
                "status": "stale",
                "chunk_id": chunk_id,
                "units": 0,
                "message": "chunk already completed by another worker",
                "checked_at": utc_now(),
            }
        if row_data["status"] not in {"assigned", "pending"} or row_data["task_status"] not in {"gathering", "active"}:
            return {
                "status": "stale",
                "chunk_id": chunk_id,
                "units": 0,
                "message": "chunk is no longer active",
                "checked_at": utc_now(),
            }
        if self.verify_chunks:
            expected = calculate_pi_segment(int(row_data["range_start"]), int(row_data["range_end"]), row_data["algorithm"])
            if segment != expected.upper():
                with self.db._lock, self.db.connect() as connection:
                    connection.execute(
                        """
                        UPDATE pool_chunks
                        SET status = 'pending', worker_id = NULL, assigned_at = NULL
                        WHERE chunk_id = ? AND status != 'completed'
                        """,
                        (chunk_id,),
                    )
                self.db.event(
                    "warning",
                    "worker submitted invalid segment",
                    {"worker_id": worker_id, "chunk_id": chunk_id},
                )
                raise ValueError("submitted segment failed pool verification")

        with self.db._lock, self.db.connect() as connection:
            now = utc_now()
            units = expected_len
            cursor = connection.execute(
                """
                UPDATE pool_chunks
                SET status = 'completed', worker_id = ?, segment = ?, compute_ms = ?, submitted_at = ?
                WHERE chunk_id = ? AND status IN ('assigned', 'pending')
                """,
                (worker_id, segment, max(0, int(compute_ms or 0)), now, chunk_id),
            )
            if cursor.rowcount == 0:
                return {
                    "status": "stale",
                    "chunk_id": chunk_id,
                    "units": 0,
                    "message": "chunk already completed by another worker",
                    "checked_at": now,
                }
            connection.execute(
                """
                INSERT INTO pool_shares (share_id, worker_id, pool_task_id, chunk_id, units, credited, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (f"share_{uuid.uuid4().hex[:16]}", worker_id, row_data["pool_task_id"], chunk_id, units, now),
            )
            connection.execute(
                "UPDATE pool_workers SET last_seen_at = ? WHERE worker_id = ?",
                (now, worker_id),
            )
            remaining = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM pool_chunks
                WHERE pool_task_id = ?
                  AND status != 'completed'
                """,
                (row_data["pool_task_id"],),
            ).fetchone()["count"]
            task = connection.execute(
                "SELECT status FROM pool_tasks WHERE pool_task_id = ?",
                (row_data["pool_task_id"],),
            ).fetchone()
            ready_to_finalize = int(remaining or 0) == 0 and task is not None and task["status"] == "gathering"
        if ready_to_finalize:
            self._schedule_finalize_task(str(row_data["pool_task_id"]))
        return {"status": "accepted", "chunk_id": chunk_id, "units": units, "checked_at": utc_now()}

    def _is_full_task_race_row(self, row_data: dict[str, Any]) -> bool:
        if not self.hybrid_race:
            return False
        try:
            task_start = int(row_data["task_range_start"])
            task_end = int(row_data["task_range_end"])
            chunk_start = int(row_data["range_start"])
            chunk_end = int(row_data["range_end"])
            units = int(row_data["units"])
        except (TypeError, ValueError, KeyError):
            return False
        return chunk_start == task_start and chunk_end == task_end and units == task_end - task_start + 1

    def _credit_late_full_task_race_share(
        self,
        row_data: dict[str, Any],
        worker_id: str,
        compute_ms: int,
    ) -> dict[str, Any] | None:
        if not self._is_full_task_race_row(row_data):
            return None
        if row_data.get("task_status") not in {"gathering", "submitting"}:
            return None

        winner_ms = max(1, int(row_data.get("compute_ms") or 0))
        worker_ms = max(1, int(compute_ms or 0))
        task_units = max(1, int(row_data.get("units") or 0))
        credit_units = max(0, min(task_units, (task_units * winner_ms) // worker_ms))
        if credit_units <= 0:
            return {
                "status": "stale",
                "chunk_id": row_data["chunk_id"],
                "units": 0,
                "message": "full task race submit was too slow for share credit",
                "checked_at": utc_now(),
            }

        now = utc_now()
        with self.db._lock, self.db.connect() as connection:
            current = connection.execute(
                """
                SELECT c.status, c.compute_ms, t.status AS task_status
                FROM pool_chunks c
                JOIN pool_tasks t ON t.pool_task_id = c.pool_task_id
                WHERE c.chunk_id = ?
                """,
                (row_data["chunk_id"],),
            ).fetchone()
            if current is None or current["status"] != "completed" or current["task_status"] not in {"gathering", "submitting"}:
                return None
            existing = connection.execute(
                """
                SELECT 1
                FROM pool_shares
                WHERE pool_task_id = ?
                  AND chunk_id = ?
                  AND worker_id = ?
                LIMIT 1
                """,
                (row_data["pool_task_id"], row_data["chunk_id"], worker_id),
            ).fetchone()
            if existing is not None:
                return {
                    "status": "stale",
                    "chunk_id": row_data["chunk_id"],
                    "units": 0,
                    "message": "worker already credited for full task race",
                    "checked_at": now,
                }
            connection.execute(
                """
                INSERT INTO pool_shares (share_id, worker_id, pool_task_id, chunk_id, units, credited, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    f"share_{uuid.uuid4().hex[:16]}",
                    worker_id,
                    row_data["pool_task_id"],
                    row_data["chunk_id"],
                    credit_units,
                    now,
                ),
            )
            connection.execute(
                "UPDATE pool_workers SET last_seen_at = ? WHERE worker_id = ?",
                (now, worker_id),
            )
            connection.execute(
                """
                INSERT INTO pool_events (created_at, level, message, payload_json)
                VALUES (?, 'info', 'pool full task race share credited', ?)
                """,
                (
                    now,
                    json_dumps(
                        {
                            "chunk_id": row_data["chunk_id"],
                            "credit_units": credit_units,
                            "mainnet_task_id": row_data.get("mainnet_task_id"),
                            "pool_task_id": row_data["pool_task_id"],
                            "task_units": task_units,
                            "winner_compute_ms": winner_ms,
                            "worker_compute_ms": worker_ms,
                            "worker_id": worker_id,
                        }
                    ),
                ),
            )
        return {
            "status": "accepted",
            "chunk_id": row_data["chunk_id"],
            "units": credit_units,
            "message": "full task race share credited",
            "checked_at": now,
        }

    def _normalize_payout_address(self, payout_address: str | None) -> str | None:
        if payout_address is None:
            return None
        normalized = str(payout_address).strip().upper()
        if not normalized:
            return None
        if not is_valid_address(normalized):
            raise ValueError("payout_address must be a valid PI wallet address")
        return normalized

    def ensure_active_task(self) -> None:
        with self.db._lock, self.db.connect() as connection:
            active = connection.execute(
                """
                SELECT mainnet_task_id
                FROM pool_tasks
                WHERE status IN ('active', 'gathering', 'submitting')
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
            if active is not None:
                return

        try:
            task = get_task_for_identity(self.server_url, self.identity)
        except TaskUnavailable as exc:
            payload: dict[str, Any] = {"status": "unavailable", "detail": exc.detail}
            if exc.retry_after_seconds is not None:
                payload["retry_after_seconds"] = exc.retry_after_seconds
            self.db.event("info", "mainnet did not assign pool work", payload)
            return
        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            if getattr(response, "status_code", None) == 429:
                self.db.event("info", "mainnet did not assign pool work", {"status": "unavailable", "error": str(exc)})
                return
            self.db.event("warning", "mainnet task request failed", {"error": str(exc)})
            return
        except requests.RequestException as exc:
            self.db.event("warning", "mainnet task request network error", {"error": str(exc)})
            return

        if task.get("status") != "assigned":
            self.db.event("info", "mainnet did not assign pool work", {"status": task.get("status")})
            return

        pool_task_id = f"pooltask_{uuid.uuid4().hex[:16]}"
        range_start = int(task["range_start"])
        range_end = int(task["range_end"])
        now = utc_now()
        with self.db._lock, self.db.connect() as connection:
            chunks, chunk_size, active_workers, task_units, target_chunks, chunk_strategy = self._resolve_chunks(
                connection,
                range_start,
                range_end,
            )
            try:
                connection.execute(
                    """
                    INSERT INTO pool_tasks (
                        pool_task_id, mainnet_task_id, status, range_start, range_end,
                        algorithm, raw_task_json, created_at
                    )
                    VALUES (?, ?, 'gathering', ?, ?, ?, ?, ?)
                    """,
                    (
                        pool_task_id,
                        task["task_id"],
                        task["range_start"],
                        task["range_end"],
                        task["algorithm"],
                        json_dumps(task),
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                return
            connection.executemany(
                """
                INSERT INTO pool_chunks (
                    chunk_id, pool_task_id, status, range_start, range_end, units
                )
                VALUES (?, ?, 'pending', ?, ?, ?)
                """,
                [
                    (
                        f"chunk_{uuid.uuid4().hex[:16]}",
                        pool_task_id,
                        chunk.range_start,
                        chunk.range_end,
                        chunk.units,
                    )
                    for chunk in chunks
                ],
            )
        self.db.event(
            "info",
            "pool task created",
            {
                "active_workers": active_workers,
                "chunk_mode": self._configured_chunk_mode(),
                "chunk_strategy": chunk_strategy,
                "chunk_size": chunk_size,
                "target_chunks": target_chunks,
                "chunks": len(chunks),
                "mainnet_task_id": task["task_id"],
                "pool_task_id": pool_task_id,
                "task_units": task_units,
                "speculative_chunks": self.speculative_chunks,
            },
        )

    def _resolve_chunks(
        self,
        connection: sqlite3.Connection,
        range_start: int,
        range_end: int,
    ) -> tuple[list[Any], int, int, int, int, str]:
        task_units = range_end - range_start + 1
        if task_units <= 0:
            raise ValueError("task range_end must be >= range_start")
        if self.chunk_size:
            chunks = split_range(range_start, range_end, self.chunk_size)
            return chunks, self.chunk_size, 0, task_units, len(chunks), "fixed"

        active_workers = self._active_worker_count(connection)
        if self.hybrid_race and task_units > active_workers:
            chunks = [WorkChunk(range_start, range_end)]
            return chunks, task_units, active_workers, task_units, 1, "full_task_race"

        target_chunks = task_units
        chunks = split_range_balanced(range_start, range_end, target_chunks)
        chunk_size = max(chunk.units for chunk in chunks)
        return chunks, chunk_size, active_workers, task_units, target_chunks, "one_unit_speculative_queue"

    def _active_worker_count(self, connection: sqlite3.Connection, window_seconds: int = 300) -> int:
        cutoff = time.time() - max(1, int(window_seconds))
        rows = connection.execute("SELECT last_seen_at FROM pool_workers").fetchall()
        count = sum(1 for row in rows if parse_iso_timestamp(row["last_seen_at"]) >= cutoff)
        return max(1, count)

    def expire_stale_assignments(self) -> None:
        cutoff = time.time() - self.chunk_timeout_seconds
        with self.db._lock, self.db.connect() as connection:
            rows = connection.execute(
                "SELECT chunk_id, assigned_at FROM pool_chunks WHERE status = 'assigned'"
            ).fetchall()
            expired = []
            for row in rows:
                try:
                    assigned_at = datetime.fromisoformat(row["assigned_at"]).timestamp()
                except (TypeError, ValueError):
                    assigned_at = 0
                if assigned_at < cutoff:
                    expired.append(row["chunk_id"])
            for chunk_id in expired:
                connection.execute(
                    """
                    UPDATE pool_chunks
                    SET status = 'pending', worker_id = NULL, assigned_at = NULL
                    WHERE chunk_id = ?
                    """,
                    (chunk_id,),
                )
            if expired:
                self.db.event("info", "expired stale worker chunks", {"count": len(expired)})

    def finalize_ready_tasks(self) -> None:
        with self.db._lock, self.db.connect() as connection:
            tasks = connection.execute(
                """
                SELECT t.*
                FROM pool_tasks t
                WHERE t.status = 'gathering'
                  AND NOT EXISTS (
                    SELECT 1 FROM pool_chunks c
                    WHERE c.pool_task_id = t.pool_task_id
                      AND c.status != 'completed'
                  )
                """
            ).fetchall()

        for task in tasks:
            self.finalize_task(dict(task))

    def _schedule_finalize_task(self, pool_task_id: str) -> None:
        thread = threading.Thread(
            target=self._finalize_task_by_id,
            args=(pool_task_id,),
            name=f"picoin-pool-finalize-{pool_task_id}",
            daemon=True,
        )
        thread.start()

    def _finalize_task_by_id(self, pool_task_id: str) -> None:
        try:
            with self.db._lock, self.db.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM pool_tasks WHERE pool_task_id = ?",
                    (pool_task_id,),
                ).fetchone()
            if row is not None:
                self.finalize_task(dict(row))
        except Exception as exc:  # pragma: no cover - background safety net
            self.db.event("error", "pool task finalize thread failed", {"pool_task_id": pool_task_id, "error": str(exc)})

    def finalize_task(self, pool_task: dict[str, Any]) -> None:
        pool_task_id = pool_task["pool_task_id"]
        task = json.loads(pool_task["raw_task_json"])
        started = now_perf()
        try:
            with self.db._lock, self.db.connect() as connection:
                cursor = connection.execute(
                    "UPDATE pool_tasks SET status = 'submitting' WHERE pool_task_id = ? AND status = 'gathering'",
                    (pool_task_id,),
                )
                if cursor.rowcount == 0:
                    return
                chunks = [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT range_start, range_end, segment, compute_ms
                        FROM pool_chunks
                        WHERE pool_task_id = ?
                        ORDER BY range_start ASC
                        """,
                        (pool_task_id,),
                    ).fetchall()
                ]

            segment = assemble_segment(int(pool_task["range_start"]), int(pool_task["range_end"]), chunks)
            result_hash = hash_result(segment, int(pool_task["range_start"]), int(pool_task["range_end"]), pool_task["algorithm"])
            root = merkle_root(segment, int(pool_task["range_start"]))
            worker_compute_ms = sum(int(chunk.get("compute_ms") or 0) for chunk in chunks)
            compute_ms = worker_compute_ms or elapsed_ms(started)

            challenge = commit_result(self.server_url, task, self.identity, result_hash, root, compute_ms)
            if not challenge.get("accepted"):
                raise RuntimeError(f"commit rejected: {challenge.get('message')}")
            reveal = reveal_samples(self.server_url, task, self.identity, segment, root, challenge)
            credited = bool(reveal.get("accepted") or reveal.get("status") == "validation_pending")
            reveal_status = reveal.get("status")
            reveal_block = reveal.get("block")
            status = "submitted" if credited else "rejected"
            if reveal_status == "validation_pending" and not isinstance(reveal_block, dict):
                status = "validation_pending"
            elif reveal.get("accepted") and isinstance(reveal_block, dict):
                status = "accepted"
            elif reveal_status == "validation_pending":
                status = "validation_pending"

            with self.db._lock, self.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE pool_tasks
                    SET status = ?, result_hash = ?, merkle_root = ?, compute_ms = ?,
                        raw_commit_json = ?, raw_reveal_json = ?, completed_at = ?
                    WHERE pool_task_id = ?
                    """,
                    (
                        status,
                        result_hash,
                        root,
                        compute_ms,
                        json_dumps(challenge),
                        json_dumps(reveal),
                        utc_now(),
                        pool_task_id,
                    ),
                )
                if credited:
                    connection.execute(
                        "UPDATE pool_shares SET credited = 1 WHERE pool_task_id = ?",
                        (pool_task_id,),
                    )
            self.db.event("info", "pool task submitted", {"pool_task_id": pool_task_id, "status": status})
        except Exception as exc:
            error = str(exc)
            status = "lost" if is_lost_competitive_round_error(error) else "error"
            level = "info" if status == "lost" else "error"
            message = "pool task lost competitive round" if status == "lost" else "pool task failed"
            with self.db._lock, self.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE pool_tasks
                    SET status = ?, error = ?, completed_at = ?
                    WHERE pool_task_id = ?
                    """,
                    (status, error, utc_now(), pool_task_id),
                )
            self.db.event(level, message, {"pool_task_id": pool_task_id, "error": error})

    def reconcile_won_blocks(self) -> dict[str, Any]:
        pending = self._pending_settlement_tasks()
        result: dict[str, Any] = {
            "checked": len(pending),
            "settled": 0,
            "block_limit": self.settlement_block_limit,
        }
        if not pending:
            return result

        try:
            blocks = self._fetch_recent_mainnet_blocks()
        except requests.RequestException as exc:
            self.db.event("warning", "pool settlement block lookup failed", {"error": str(exc)})
            result["error"] = str(exc)
            return result

        blocks_by_task_id: dict[str, dict[str, Any]] = {}
        blocks_by_height: dict[int, dict[str, Any]] = {}
        for block in blocks:
            if not isinstance(block, dict):
                continue
            task_id = str(block.get("task_id") or "")
            if task_id:
                blocks_by_task_id[task_id] = block
            height = self._int_or_none(block.get("height"))
            if height is not None:
                blocks_by_height[height] = block

        now = utc_now()
        for task_id, row in pending.items():
            block = blocks_by_task_id.get(task_id)
            if block is not None:
                reveal = dict(row["reveal"])
                reveal["accepted"] = True
                reveal["status"] = "accepted"
                reveal["message"] = "settled from mainnet block"
                reveal["block"] = self._normalize_block_payload(block)
                with self.db._lock, self.db.connect() as connection:
                    cursor = connection.execute(
                        """
                        UPDATE pool_tasks
                        SET status = 'accepted',
                            raw_reveal_json = ?,
                            completed_at = COALESCE(completed_at, ?)
                        WHERE pool_task_id = ?
                          AND status IN ('accepted', 'submitted', 'validation_pending')
                        """,
                        (json_dumps(reveal), now, row["pool_task_id"]),
                    )
                if cursor.rowcount:
                    result["settled"] += 1
                    self.db.event(
                        "info",
                        "pool task settled from mainnet block",
                        {
                            "pool_task_id": row["pool_task_id"],
                            "mainnet_task_id": task_id,
                            "height": block.get("height"),
                            "block_hash": block.get("block_hash") or block.get("hash"),
                        },
                    )
                continue

            round_height = row.get("competitive_round_height")
            winning_block = blocks_by_height.get(round_height) if round_height is not None else None
            winning_task_id = str((winning_block or {}).get("task_id") or "")
            if winning_block is None or not winning_task_id or winning_task_id == task_id:
                continue

            error = f"competitive round won by {winning_task_id} at block {winning_block.get('height')}"
            reveal = dict(row["reveal"])
            reveal["accepted"] = False
            reveal["status"] = "lost"
            reveal["message"] = error
            reveal["block"] = None
            with self.db._lock, self.db.connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE pool_tasks
                    SET status = 'lost',
                        error = ?,
                        raw_reveal_json = ?,
                        completed_at = COALESCE(completed_at, ?)
                    WHERE pool_task_id = ?
                      AND status IN ('submitted', 'validation_pending')
                    """,
                    (error, json_dumps(reveal), now, row["pool_task_id"]),
                )
            if cursor.rowcount:
                result["lost"] = int(result.get("lost") or 0) + 1
                self.db.event(
                    "info",
                    "pool task lost competitive round",
                    {
                        "pool_task_id": row["pool_task_id"],
                        "mainnet_task_id": task_id,
                        "winner_task_id": winning_task_id,
                        "height": winning_block.get("height"),
                        "block_hash": winning_block.get("block_hash") or winning_block.get("hash"),
                    },
                )
        return result

    def reconcile_mainnet_task_statuses(self, limit: int = 50) -> dict[str, Any]:
        rows = self._pending_mainnet_status_tasks(limit=limit)
        result: dict[str, Any] = {"checked": len(rows), "updated": 0, "errors": 0, "statuses": {}}
        for row in rows:
            task_id = str(row.get("mainnet_task_id") or "")
            if not task_id:
                continue
            try:
                payload = self._fetch_mainnet_task_status(task_id)
            except requests.RequestException as exc:
                result["errors"] += 1
                self.db.event(
                    "warning",
                    "pool task status lookup failed",
                    {"mainnet_task_id": task_id, "error": str(exc)},
                )
                continue
            status = str(payload.get("status") or "unknown")
            result["statuses"][status] = int(result["statuses"].get(status, 0)) + 1
            if self._apply_mainnet_task_status(row, payload):
                result["updated"] += 1
        return result

    def _pending_mainnet_status_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.db._lock, self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT pool_task_id, mainnet_task_id, status, raw_reveal_json
                FROM pool_tasks
                WHERE status IN ('submitted', 'validation_pending', 'unsettled')
                  AND raw_reveal_json IS NOT NULL
                ORDER BY completed_at DESC, created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _fetch_mainnet_task_status(self, task_id: str) -> dict[str, Any]:
        response = requests.get(f"{self.server_url}/tasks/{task_id}/status", timeout=20)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise requests.RequestException("task status response was not an object")
        return payload

    def _apply_mainnet_task_status(self, row: dict[str, Any], payload: dict[str, Any]) -> bool:
        status = str(payload.get("status") or "").strip()
        if not status:
            return False

        try:
            reveal = json.loads(row.get("raw_reveal_json") or "{}")
        except (TypeError, ValueError):
            reveal = {}
        if not isinstance(reveal, dict):
            reveal = {}

        block = payload.get("block")
        validation = payload.get("validation")
        message = str(payload.get("message") or f"mainnet task status is {status}")
        update_status: str | None = None
        error: str | None = None

        if status == "accepted" and isinstance(block, dict):
            update_status = "accepted"
            reveal["accepted"] = True
            reveal["status"] = "accepted"
            reveal["message"] = message
            reveal["block"] = self._normalize_block_payload(block)
            error = None
        elif status == "validation_pending":
            update_status = "validation_pending"
            reveal["accepted"] = True
            reveal["status"] = "validation_pending"
            reveal["message"] = message
            reveal["block"] = None
            error = None
        elif status in {"expired", "stale", "rejected"}:
            update_status = status
            reveal["accepted"] = False
            reveal["status"] = status
            reveal["message"] = message
            reveal["block"] = None
            error = message
        else:
            return False

        if isinstance(validation, dict):
            reveal["validation"] = validation

        with self.db._lock, self.db.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE pool_tasks
                SET status = ?,
                    error = ?,
                    raw_reveal_json = ?,
                    completed_at = COALESCE(completed_at, ?)
                WHERE pool_task_id = ?
                  AND status IN ('submitted', 'validation_pending', 'unsettled')
                """,
                (update_status, error, json_dumps(reveal), utc_now(), row["pool_task_id"]),
            )
        if cursor.rowcount:
            self.db.event(
                "info",
                "pool task status reconciled from mainnet",
                {
                    "pool_task_id": row["pool_task_id"],
                    "mainnet_task_id": row["mainnet_task_id"],
                    "status": update_status,
                },
            )
        return bool(cursor.rowcount)

    def _pending_settlement_tasks(self) -> dict[str, dict[str, Any]]:
        pending: dict[str, dict[str, Any]] = {}
        with self.db._lock, self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT pool_task_id, mainnet_task_id, status, raw_task_json, raw_reveal_json
                FROM pool_tasks
                WHERE raw_reveal_json IS NOT NULL
                  AND status IN ('accepted', 'submitted', 'validation_pending')
                """
            ).fetchall()
        for row in rows:
            try:
                reveal = json.loads(row["raw_reveal_json"])
            except (TypeError, ValueError):
                continue
            if not isinstance(reveal, dict):
                continue
            block = reveal.get("block")
            if isinstance(block, dict):
                continue
            reveal_status = reveal.get("status")
            if reveal_status != "validation_pending" and row["status"] not in ("submitted", "validation_pending"):
                continue
            try:
                raw_task = json.loads(row["raw_task_json"])
            except (TypeError, ValueError):
                raw_task = {}
            pending[str(row["mainnet_task_id"])] = {
                "pool_task_id": row["pool_task_id"],
                "mainnet_task_id": row["mainnet_task_id"],
                "reveal": reveal,
                "competitive_round_height": self._int_or_none(raw_task.get("competitive_round_height")),
            }
        return pending

    def _fetch_recent_mainnet_blocks(self) -> list[dict[str, Any]]:
        response = requests.get(
            f"{self.server_url}/blocks",
            params={"limit": self.settlement_block_limit},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        blocks = payload.get("blocks", payload) if isinstance(payload, dict) else payload
        if not isinstance(blocks, list):
            return []
        return [block for block in blocks if isinstance(block, dict)]

    def _normalize_block_payload(self, block: dict[str, Any]) -> dict[str, Any]:
        payload = dict(block)
        if "block_hash" not in payload and "hash" in payload:
            payload["block_hash"] = payload.get("hash")
        return payload

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def stats(self) -> dict[str, Any]:
        with self.db._lock, self.db.connect() as connection:
            workers = connection.execute("SELECT COUNT(*) AS count FROM pool_workers").fetchone()["count"]
            worker_rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT worker_id, name, payout_address, registered_at, last_seen_at
                    FROM pool_workers
                    ORDER BY last_seen_at DESC
                    LIMIT 250
                    """
                ).fetchall()
            ]
            task_rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT status, error, raw_task_json, raw_reveal_json, completed_at
                    FROM pool_tasks
                    """
                ).fetchall()
            ]
            task_counts: dict[str, int] = {}
            for row in task_rows:
                status = self._display_task_status(row)
                task_counts[status] = task_counts.get(status, 0) + 1
            tasks = [
                {"status": status, "count": count}
                for status, count in sorted(task_counts.items(), key=lambda item: item[0])
            ]
            chunks = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM pool_chunks
                    GROUP BY status
                    ORDER BY status
                    """
                ).fetchall()
            ]
            share_rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT worker_id, pool_task_id, units, created_at
                    FROM pool_shares
                    WHERE credited = 1
                    """
                ).fetchall()
            ]
            task_rewards = self._accepted_task_rewards(connection)
            latest_reward_window = self._latest_reward_window(task_rewards)
            current_round_share_rows = filter_shares_by_window(
                share_rows,
                start_at=latest_reward_window["current_start_at"],
            )
            last_round_share_rows = filter_shares_by_window(
                share_rows,
                start_at=latest_reward_window["last_start_at"],
                end_at=latest_reward_window["last_end_at"],
            )
            validation_pending_tasks = self._validation_pending_task_count(connection)
            payout_rows = self._paid_payout_rows(connection)
            payout_history = self._payout_history(connection)
            recent_events = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT created_at, level, message, payload_json
                    FROM pool_events
                    ORDER BY event_id DESC
                    LIMIT 20
                    """
                ).fetchall()
            ]
            active_worker_rows = self._active_workers(worker_rows)
            current_round_shares = summarize_shares(current_round_share_rows)
            last_round_shares = summarize_shares(last_round_share_rows)
            lifetime_shares = summarize_shares(share_rows)
            hashrate = self._hashrate_summary(connection)
            performance = self._performance_summary(
                tasks=tasks,
                chunks=chunks,
                active_worker_rows=active_worker_rows,
                credited_worker_count=len(current_round_shares),
                hashrate=hashrate,
                won_blocks=task_rewards,
                validation_pending_tasks=validation_pending_tasks,
            )
        return {
            "status": "ok",
            "miner_id": self.identity.get("miner_id"),
            "mainnet_server": self.server_url,
            "workers": workers,
            "worker_details": worker_rows,
            "active_workers": len(active_worker_rows),
            "active_worker_window_seconds": 300,
            "active_worker_details": active_worker_rows,
            "chunking": {
                "mode": self._configured_chunk_mode(),
                "fixed_chunk_size": self.chunk_size,
                "active_worker_window_seconds": 300,
                "strategy": self._configured_chunk_strategy(),
                "hybrid_full_task_when": "task_units > active_workers" if self.hybrid_race else None,
                "target_chunks_per_active_worker": None,
                "target_units_per_chunk": None if self.hybrid_race else (1 if self.chunk_size is None else self.chunk_size),
                "speculative_assignment": self.speculative_chunks,
            },
            "settlement": {
                "block_limit": self.settlement_block_limit,
            },
            "hashrate": hashrate,
            "tasks": tasks,
            "chunks": chunks,
            "credited_shares": current_round_shares,
            "current_round_shares": current_round_shares,
            "last_round_shares": last_round_shares,
            "lifetime_shares": lifetime_shares,
            "share_windows": latest_reward_window,
            "won_blocks": task_rewards[:20],
            "performance": performance,
            "active_tasks": performance["active_tasks"],
            "completed_tasks": performance["completed_tasks"],
            "payouts": summarize_round_window_payouts(
                task_rewards=task_rewards,
                share_rows=share_rows,
                worker_rows=worker_rows,
                payout_rows=payout_rows,
                pool_fee_percent=self.pool_fee_percent,
                min_payout_amount=self.payout_min_amount,
            ),
            "auto_payouts": self.auto_payout_config(),
            "payout_history": payout_history,
            "events": recent_events,
            "checked_at": utc_now(),
        }

    def payouts(self) -> dict[str, Any]:
        stats = self.stats()
        return {
            "status": "ok",
            "miner_id": self.identity.get("miner_id"),
            "auto_payouts": stats["auto_payouts"],
            "payouts": stats["payouts"],
            "history": stats["payout_history"],
            "checked_at": utc_now(),
        }

    def workers(self) -> dict[str, Any]:
        with self.db._lock, self.db.connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT worker_id, name, payout_address, registered_at, last_seen_at
                    FROM pool_workers
                    ORDER BY last_seen_at DESC
                    LIMIT 250
                    """
                ).fetchall()
            ]
        return {"status": "ok", "count": len(rows), "workers": rows, "checked_at": utc_now()}

    def _display_task_status(self, row: dict[str, Any]) -> str:
        status = str(row.get("status") or "unknown")
        error = str(row.get("error") or "")
        if status == "error" and is_lost_competitive_round_error(error):
            return "lost"
        if status == "unsettled":
            return "unsettled"

        try:
            reveal = json.loads(row.get("raw_reveal_json") or "{}")
        except (TypeError, ValueError):
            reveal = {}
        if not isinstance(reveal, dict):
            reveal = {}

        block = reveal.get("block")
        reveal_status = reveal.get("status")
        if reveal_status == "validation_pending" and not isinstance(block, dict):
            return "validation_pending"
        return status

    def _accepted_task_rewards(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rewards: list[dict[str, Any]] = []
        rows = connection.execute(
            """
            SELECT pool_task_id, mainnet_task_id, raw_reveal_json, completed_at
            FROM pool_tasks
            WHERE status = 'accepted'
              AND raw_reveal_json IS NOT NULL
            ORDER BY completed_at DESC
            """
        ).fetchall()
        for row in rows:
            try:
                reveal = json.loads(row["raw_reveal_json"])
            except (TypeError, ValueError):
                continue
            block = reveal.get("block") if isinstance(reveal, dict) else None
            if not isinstance(block, dict):
                continue
            reward = block.get("reward")
            try:
                reward_amount = float(reward)
            except (TypeError, ValueError):
                continue
            if reward_amount <= 0:
                continue
            rewards.append(
                {
                    "pool_task_id": row["pool_task_id"],
                    "mainnet_task_id": row["mainnet_task_id"],
                    "height": block.get("height"),
                    "block_hash": block.get("block_hash"),
                    "reward": reward_amount,
                    "completed_at": row["completed_at"],
                }
            )
        return rewards

    @staticmethod
    def _latest_reward_window(task_rewards: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(task_rewards, key=lambda row: parse_iso_timestamp(row.get("completed_at")))
        if not ordered:
            return {
                "current_start_at": None,
                "last_start_at": None,
                "last_end_at": None,
            }
        latest = ordered[-1]
        previous = ordered[-2] if len(ordered) >= 2 else None
        return {
            "current_start_at": latest.get("completed_at"),
            "last_start_at": previous.get("completed_at") if previous else None,
            "last_end_at": latest.get("completed_at"),
        }

    def _validation_pending_task_count(self, connection: sqlite3.Connection) -> int:
        rows = connection.execute(
            """
            SELECT status, raw_reveal_json
            FROM pool_tasks
            WHERE raw_reveal_json IS NOT NULL
              AND status = 'validation_pending'
            """
        ).fetchall()
        pending = 0
        for row in rows:
            try:
                reveal = json.loads(row["raw_reveal_json"])
            except (TypeError, ValueError):
                continue
            block = reveal.get("block") if isinstance(reveal, dict) else None
            reveal_status = reveal.get("status") if isinstance(reveal, dict) else None
            if block is None and reveal_status == "validation_pending":
                pending += 1
        return pending

    def _active_workers(self, worker_rows: list[dict[str, Any]], window_seconds: int = 300) -> list[dict[str, Any]]:
        cutoff = time.time() - max(1, int(window_seconds))
        active = [row for row in worker_rows if parse_iso_timestamp(row.get("last_seen_at")) >= cutoff]
        active.sort(key=lambda row: str(row.get("last_seen_at") or ""), reverse=True)
        return active

    def _hashrate_summary(self, connection: sqlite3.Connection, window_seconds: int = 300) -> dict[str, Any]:
        cutoff = time.time() - max(1, int(window_seconds))
        latest_by_worker: dict[str, dict[str, Any]] = {}
        rows = connection.execute(
            """
            SELECT worker_id, units, compute_ms, submitted_at
            FROM pool_chunks
            WHERE status = 'completed'
              AND worker_id IS NOT NULL
              AND COALESCE(compute_ms, 0) > 0
              AND submitted_at IS NOT NULL
            ORDER BY submitted_at DESC
            """
        ).fetchall()
        for row in rows:
            worker_id = str(row["worker_id"] or "")
            if not worker_id or worker_id in latest_by_worker:
                continue
            submitted_at = parse_iso_timestamp(row["submitted_at"])
            if submitted_at < cutoff:
                continue
            units = int(row["units"] or 0)
            compute_ms = int(row["compute_ms"] or 0)
            if units <= 0 or compute_ms <= 0:
                continue
            rate = units / (compute_ms / 1000)
            latest_by_worker[worker_id] = {
                "worker_id": worker_id,
                "hashrate_hps": round(rate, 4),
                "units": units,
                "compute_ms": compute_ms,
                "submitted_at": row["submitted_at"],
            }

        worker_rates = sorted(latest_by_worker.values(), key=lambda item: float(item["hashrate_hps"]), reverse=True)
        pool_hashrate = sum(float(row["hashrate_hps"]) for row in worker_rates)
        return {
            "window_seconds": max(1, int(window_seconds)),
            "pool_hashrate_hps": round(pool_hashrate, 4),
            "active_hashrate_workers": len(worker_rates),
            "avg_worker_hashrate_hps": round(pool_hashrate / len(worker_rates), 4) if worker_rates else 0.0,
            "workers": worker_rates,
        }

    def _performance_summary(
        self,
        *,
        tasks: list[dict[str, Any]],
        chunks: list[dict[str, Any]],
        active_worker_rows: list[dict[str, Any]],
        credited_worker_count: int,
        hashrate: dict[str, Any],
        won_blocks: list[dict[str, Any]],
        validation_pending_tasks: int,
    ) -> dict[str, Any]:
        task_counts = {str(row["status"]): int(row.get("count") or 0) for row in tasks}
        chunk_counts = {str(row["status"]): int(row.get("count") or 0) for row in chunks}
        blocks_won = len(won_blocks)
        lost_rounds = int(task_counts.get("lost", 0))
        non_winning_rounds = sum(int(task_counts.get(status, 0)) for status in ("lost", "stale", "expired", "rejected"))
        finished_competitive_rounds = blocks_won + non_winning_rounds
        win_rate = (blocks_won / finished_competitive_rounds) if finished_competitive_rounds else 0.0
        active_tasks = sum(int(task_counts.get(status, 0)) for status in ("active", "gathering", "submitting"))
        completed_tasks = max(0, sum(task_counts.values()) - active_tasks - validation_pending_tasks)
        return {
            "active_workers": len(active_worker_rows),
            "pool_hashrate_hps": float(hashrate.get("pool_hashrate_hps") or 0.0),
            "active_hashrate_workers": int(hashrate.get("active_hashrate_workers") or 0),
            "active_worker_window_seconds": 300,
            "credited_workers": credited_worker_count,
            "blocks_won": blocks_won,
            "validation_pending_tasks": validation_pending_tasks,
            "unsettled_tasks": int(task_counts.get("unsettled", 0)),
            "lost_rounds": lost_rounds,
            "non_winning_rounds": non_winning_rounds,
            "finished_competitive_rounds": finished_competitive_rounds,
            "win_rate": round(win_rate, 6),
            "win_rate_percent": round(win_rate * 100, 2),
            "active_tasks": active_tasks,
            "completed_tasks": completed_tasks,
            "available_chunks": int(chunk_counts.get("pending", 0)),
            "assigned_chunks": int(chunk_counts.get("assigned", 0)),
            "completed_chunks": int(chunk_counts.get("completed", 0)),
        }

    def _paid_payout_rows(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT worker_id, payout_address, amount
                FROM pool_payouts
                WHERE status IN ('submitting', 'submitted', 'confirmed')
                """
            ).fetchall()
        ]

    def _payout_history(self, connection: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT payout_id, worker_id, payout_address, amount, fee, tx_hash, status,
                       error, created_at, submitted_at, updated_at
                FROM pool_payouts
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]

    def auto_payout_config(self) -> dict[str, Any]:
        return {
            "enabled": self.payout_wallet is not None,
            "wallet_address": self.payout_wallet.get("address") if self.payout_wallet else None,
            "interval_seconds": self.payout_interval_seconds,
            "min_amount": self.payout_min_amount,
            "fee": self.payout_fee,
            "confirmation_grace_seconds": self.payout_confirmation_grace_seconds,
        }

    def maybe_run_payouts(self) -> None:
        if self.payout_wallet is None:
            return
        current = time.monotonic()
        if self._last_payout_attempt and current - self._last_payout_attempt < self.payout_interval_seconds:
            return
        self._last_payout_attempt = current
        result = self.run_payouts()
        if result["submitted"] or result["errors"]:
            self.db.event("info", "auto payout run completed", result)

    def run_payouts(self) -> dict[str, Any]:
        if self.payout_wallet is None:
            return {"enabled": False, "eligible": 0, "submitted": 0, "errors": 0}

        self.reconcile_payout_statuses()
        stats = self.stats()
        workers = [
            worker
            for worker in stats["payouts"].get("workers", [])
            if worker.get("payable") and worker.get("payout_address")
        ]
        result: dict[str, Any] = {
            "enabled": True,
            "eligible": len(workers),
            "submitted": 0,
            "errors": 0,
            "tx_hashes": [],
        }
        if not workers:
            return result

        nonce = self._fetch_wallet_nonce()
        min_units = to_units(self.payout_min_amount)
        fee_units = to_units(self.payout_fee)
        for worker in workers:
            payout_address = self._normalize_payout_address(worker.get("payout_address"))
            amount_units = floor_picoin_units(worker.get("pending_amount") or 0)
            if amount_units < min_units:
                continue
            payout_id = f"payout_{uuid.uuid4().hex[:16]}"
            amount = units_to_float(amount_units)
            tx = sign_transaction(
                private_key=self.payout_wallet["private_key"],
                public_key=self.payout_wallet["public_key"],
                tx_type="transfer",
                sender=self.payout_wallet["address"],
                recipient=payout_address,
                amount=amount,
                fee=self.payout_fee,
                nonce=nonce,
                payload={
                    "source": "picoin-pool",
                    "pool_miner_id": self.identity.get("miner_id"),
                    "worker_id": worker["worker_id"],
                    "payout_id": payout_id,
                },
            )
            now = utc_now()
            with self.db._lock, self.db.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO pool_payouts (
                        payout_id, worker_id, payout_address, amount, amount_units,
                        fee, fee_units, tx_hash, status, raw_tx_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'submitting', ?, ?, ?)
                    """,
                    (
                        payout_id,
                        worker["worker_id"],
                        payout_address,
                        amount,
                        amount_units,
                        units_to_float(fee_units),
                        fee_units,
                        tx["tx_hash"],
                        json_dumps(tx),
                        now,
                        now,
                    ),
                )
            try:
                response = self._submit_payout_transaction(tx)
                submitted_at = utc_now()
                with self.db._lock, self.db.connect() as connection:
                    connection.execute(
                        """
                        UPDATE pool_payouts
                        SET status = 'submitted', raw_response_json = ?, submitted_at = ?, updated_at = ?
                        WHERE payout_id = ?
                        """,
                        (json_dumps(response), submitted_at, submitted_at, payout_id),
                    )
                result["submitted"] += 1
                result["tx_hashes"].append(tx["tx_hash"])
                nonce += 1
            except Exception as exc:
                updated_at = utc_now()
                with self.db._lock, self.db.connect() as connection:
                    connection.execute(
                        """
                        UPDATE pool_payouts
                        SET status = 'error', error = ?, updated_at = ?
                        WHERE payout_id = ?
                        """,
                        (str(exc), updated_at, payout_id),
                    )
                result["errors"] += 1
                self.db.event(
                    "error",
                    "auto payout failed",
                    {"worker_id": worker["worker_id"], "payout_id": payout_id, "error": str(exc)},
                )
                break
        return result

    def reconcile_payout_statuses(self, limit: int = 100) -> dict[str, Any]:
        with self.db._lock, self.db.connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT payout_id, tx_hash, status, created_at, updated_at
                    FROM pool_payouts
                    WHERE status IN ('submitting', 'submitted')
                    ORDER BY created_at ASC
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
            ]

        result = {"checked": len(rows), "confirmed": 0, "pending": 0, "expired": 0, "errors": 0}
        for row in rows:
            tx_hash = str(row.get("tx_hash") or "").strip()
            if not tx_hash:
                continue
            try:
                payload = self._fetch_payout_transaction(tx_hash)
            except requests.RequestException as exc:
                result["errors"] += 1
                self.db.event(
                    "warning",
                    "payout transaction status lookup failed",
                    {"payout_id": row.get("payout_id"), "tx_hash": tx_hash, "error": str(exc)},
                )
                continue

            status = str(payload.get("status") or "").strip().lower()
            detail = str(payload.get("detail") or payload.get("message") or "").strip()
            empty_tx_response = not status and not any(
                payload.get(key)
                for key in ("tx_hash", "hash", "sender", "recipient", "amount", "block_height")
            )
            not_found = status in {"not_found", "missing"} or "not found" in detail.lower() or empty_tx_response
            age_seconds = time.time() - parse_iso_timestamp(str(row.get("created_at") or row.get("updated_at") or ""))

            if status == "confirmed":
                self._update_payout_status(
                    str(row["payout_id"]),
                    "confirmed",
                    raw_response=payload,
                    error=None,
                )
                result["confirmed"] += 1
                continue
            if status in {"pending", "selected", "submitted", "mempool"}:
                self._update_payout_status(
                    str(row["payout_id"]),
                    "submitted",
                    raw_response=payload,
                    error=None,
                )
                result["pending"] += 1
                continue
            if not_found and age_seconds < self.payout_confirmation_grace_seconds:
                result["pending"] += 1
                continue

            if not_found:
                error = f"payout transaction not found after {int(age_seconds)} seconds"
            elif status in {"expired", "failed", "rejected", "error"}:
                error = detail or f"payout transaction status is {status}"
            else:
                continue

            self._update_payout_status(
                str(row["payout_id"]),
                "error",
                raw_response=payload,
                error=error,
            )
            result["expired"] += 1
            self.db.event(
                "warning",
                "payout transaction released back to pending balance",
                {"payout_id": row.get("payout_id"), "tx_hash": tx_hash, "error": error},
            )
        return result

    def _update_payout_status(
        self,
        payout_id: str,
        status: str,
        *,
        raw_response: dict[str, Any],
        error: str | None,
    ) -> None:
        now = utc_now()
        with self.db._lock, self.db.connect() as connection:
            connection.execute(
                """
                UPDATE pool_payouts
                SET status = ?,
                    raw_response_json = ?,
                    error = ?,
                    updated_at = ?
                WHERE payout_id = ?
                """,
                (status, json_dumps(raw_response), error, now, payout_id),
            )

    def _fetch_wallet_nonce(self) -> int:
        response = requests.get(
            f"{self.server_url}/wallet/{self.payout_wallet['address']}/nonce",
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        return int(payload.get("next_nonce") or payload.get("nonce") or 1)

    def _submit_payout_transaction(self, tx: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for path in ("/tx/submit", "/transactions/submit"):
            response = requests.post(f"{self.server_url}{path}", json=tx, timeout=30)
            if response.status_code == HTTPStatus.NOT_FOUND:
                last_error = requests.HTTPError(f"404 Client Error: Not Found for url: {response.url}", response=response)
                continue
            response.raise_for_status()
            return response.json()
        if last_error is not None:
            raise last_error
        raise RuntimeError("no transaction submit endpoint returned a response")

    def _fetch_payout_transaction(self, tx_hash: str) -> dict[str, Any]:
        response = requests.get(f"{self.server_url}/tx/{tx_hash}", timeout=20)
        if response.status_code == HTTPStatus.NOT_FOUND:
            return {"status": "not_found", "detail": "transaction not found"}
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise requests.RequestException("transaction status response was not an object")
        return payload


class PoolHandler(BaseHTTPRequestHandler):
    coordinator: PoolCoordinator
    auth_token: str | None = None
    public_workers: bool = False

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_common_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/health":
                self.send_json({"status": "ok", "checked_at": utc_now()})
            elif parsed.path == "/stats":
                self.send_json(self.coordinator.stats())
            elif parsed.path == "/payouts":
                self.send_json(self.coordinator.payouts())
            elif parsed.path == "/workers":
                self.send_json(self.coordinator.workers())
            elif parsed.path == "/work/next":
                self.require_worker_auth()
                query = parse_qs(parsed.query)
                worker_id = (query.get("worker_id") or [""])[0]
                self.send_json(self.coordinator.claim_work(worker_id))
            else:
                self.send_json({"detail": "not found"}, HTTPStatus.NOT_FOUND)
        except PermissionError as exc:
            self.send_json({"detail": str(exc)}, HTTPStatus.UNAUTHORIZED)
        except Exception as exc:
            self.send_json({"detail": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/workers/register", "/work/submit"}:
                self.require_worker_auth()
            payload = self.read_json()
            if parsed.path == "/workers/register":
                self.send_json(
                    self.coordinator.register_worker(
                        str(payload.get("worker_id") or ""),
                        payload.get("name"),
                        payload.get("payout_address"),
                    )
                )
            elif parsed.path == "/work/submit":
                self.send_json(
                    self.coordinator.submit_work(
                        str(payload.get("worker_id") or ""),
                        str(payload.get("chunk_id") or ""),
                        str(payload.get("segment") or ""),
                        int(payload.get("compute_ms") or 0),
                    )
                )
            else:
                self.send_json({"detail": "not found"}, HTTPStatus.NOT_FOUND)
        except PermissionError as exc:
            self.send_json({"detail": str(exc)}, HTTPStatus.UNAUTHORIZED)
        except Exception as exc:
            self.send_json({"detail": str(exc)}, HTTPStatus.BAD_REQUEST)

    def require_auth(self) -> None:
        if not self.auth_token:
            return
        bearer = self.headers.get("Authorization", "")
        token = self.headers.get("X-Picoin-Pool-Token", "")
        if bearer == f"Bearer {self.auth_token}" or token == self.auth_token:
            return
        raise PermissionError("invalid pool token")

    def require_worker_auth(self) -> None:
        if self.public_workers:
            return
        self.require_auth()

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, payload: dict[str, Any], status: int | HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(int(status))
        self.send_common_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Picoin-Pool-Token")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Picoin pool server alpha.")
    parser.add_argument("--server", default="https://api.picoin.science", help="Picoin API used by the pool miner")
    parser.add_argument("--identity", type=Path, default=Path("picoin-pool/pool_identity.json"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9321)
    parser.add_argument("--db", type=Path, default=Path("picoin-pool/pool.sqlite3"))
    parser.add_argument(
        "--chunk-size",
        default="auto",
        help=(
            "Pool chunk size. Use 'auto' for one unit per chunk, 'hybrid-race' to use full-task racing "
            "when task units exceed active workers, or a positive integer for fixed chunks."
        ),
    )
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--chunk-timeout-seconds", type=int, default=45)
    parser.add_argument("--pool-name", default="picoin-pool")
    parser.add_argument("--auth-token", default=os.getenv("PICOIN_POOL_TOKEN", ""))
    parser.add_argument("--public-workers", action="store_true", help="Allow workers to register, claim, and submit without a shared token")
    parser.add_argument("--require-worker-payout", action="store_true", help="Require every worker to register a valid PI payout address")
    parser.add_argument("--pool-fee-percent", type=float, default=1.0, help="Operator fee shown in payout accounting")
    parser.add_argument("--payout-wallet", type=Path, default=None, help="Wallet JSON used to sign automatic worker payouts")
    parser.add_argument("--payout-interval-seconds", type=int, default=7200, help="Seconds between automatic payout runs")
    parser.add_argument("--payout-min-amount", type=float, default=0.1, help="Minimum worker pending balance before auto payout")
    parser.add_argument("--payout-fee", type=float, default=0.0, help="Network fee attached to each automatic payout transaction")
    parser.add_argument(
        "--payout-confirmation-grace-seconds",
        type=int,
        default=600,
        help="Seconds to keep an unconfirmed/not-found payout reserved before retrying it",
    )
    parser.add_argument("--settlement-block-limit", type=int, default=100, help="Recent mainnet blocks checked when settling validation-pending pool tasks")
    parser.add_argument(
        "--disable-speculative-chunks",
        action="store_true",
        help="Disable duplicate assignment of already-assigned chunks when workers are idle",
    )
    parser.add_argument("--trust-workers", action="store_true", help="Skip pool-side chunk verification")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    identity = load_or_register_identity(args.server.rstrip("/"), args.identity, default_name=args.pool_name)
    payout_wallet = load_payout_wallet(args.payout_wallet)
    db = PoolDatabase(args.db)
    coordinator = PoolCoordinator(
        db=db,
        server_url=args.server,
        identity=identity,
        chunk_size=args.chunk_size,
        poll_seconds=args.poll_seconds,
        chunk_timeout_seconds=args.chunk_timeout_seconds,
        verify_chunks=not args.trust_workers,
        require_worker_payout=bool(args.require_worker_payout or args.public_workers),
        pool_fee_percent=args.pool_fee_percent,
        speculative_chunks=not args.disable_speculative_chunks,
        payout_wallet=payout_wallet,
        payout_interval_seconds=args.payout_interval_seconds,
        payout_min_amount=args.payout_min_amount,
        payout_fee=args.payout_fee,
        payout_confirmation_grace_seconds=args.payout_confirmation_grace_seconds,
        settlement_block_limit=args.settlement_block_limit,
    )
    coordinator.start()
    PoolHandler.coordinator = coordinator
    PoolHandler.auth_token = args.auth_token.strip() or None
    PoolHandler.public_workers = bool(args.public_workers)
    httpd = ThreadingHTTPServer((args.host, args.port), PoolHandler)
    print(f"Picoin pool server listening on http://{args.host}:{args.port}")
    print(f"Pool miner identity: {identity.get('miner_id')}")
    if payout_wallet:
        print(f"Auto payouts enabled from wallet: {payout_wallet['address']}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        coordinator.stop_event.set()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
