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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from pool_accounting import assemble_segment, split_range, summarize_shares


REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = Path(os.getenv("PICOIN_CORE_PATH", REPO_ROOT / "picoin-proof-of-pi"))
if str(CORE_PATH) not in sys.path:
    sys.path.insert(0, str(CORE_PATH))

from app.core.crypto import hash_result  # noqa: E402
from app.core.merkle import merkle_root  # noqa: E402
from app.core.performance import elapsed_ms, now_perf  # noqa: E402
from app.core.pi import calculate_pi_segment  # noqa: E402
from miner.client import (  # noqa: E402
    commit_result,
    get_task_for_identity,
    load_or_register_identity,
    reveal_samples,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


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
        chunk_size: int,
        poll_seconds: float,
        chunk_timeout_seconds: int,
        verify_chunks: bool,
    ) -> None:
        self.db = db
        self.server_url = server_url.rstrip("/")
        self.identity = identity
        self.chunk_size = max(1, chunk_size)
        self.poll_seconds = max(0.5, poll_seconds)
        self.chunk_timeout_seconds = max(5, chunk_timeout_seconds)
        self.verify_chunks = verify_chunks
        self.stop_event = threading.Event()

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
        self.ensure_active_task()

    def register_worker(self, worker_id: str, name: str | None, payout_address: str | None) -> dict[str, Any]:
        worker_id = worker_id.strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        now = utc_now()
        with self.db._lock, self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO pool_workers (worker_id, name, payout_address, registered_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    name = COALESCE(excluded.name, pool_workers.name),
                    payout_address = COALESCE(excluded.payout_address, pool_workers.payout_address),
                    last_seen_at = excluded.last_seen_at
                """,
                (worker_id, name, payout_address, now, now),
            )
        return {"status": "ok", "worker_id": worker_id, "checked_at": utc_now()}

    def claim_work(self, worker_id: str) -> dict[str, Any]:
        worker_id = worker_id.strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        self.register_worker(worker_id, None, None)
        with self.db._lock, self.db.connect() as connection:
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
            if row is None:
                return {"status": "idle", "message": "no pool work available", "checked_at": utc_now()}
            now = utc_now()
            connection.execute(
                """
                UPDATE pool_chunks
                SET status = 'assigned', worker_id = ?, assigned_at = ?
                WHERE chunk_id = ? AND status = 'pending'
                """,
                (worker_id, now, row["chunk_id"]),
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
                "checked_at": now,
            }

    def submit_work(self, worker_id: str, chunk_id: str, segment: str, compute_ms: int) -> dict[str, Any]:
        worker_id = worker_id.strip()
        chunk_id = chunk_id.strip()
        segment = segment.strip().upper()
        with self.db._lock, self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT c.*, t.algorithm
                FROM pool_chunks c
                JOIN pool_tasks t ON t.pool_task_id = c.pool_task_id
                WHERE c.chunk_id = ?
                """,
                (chunk_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown chunk_id")
            if row["status"] != "assigned" or row["worker_id"] != worker_id:
                raise ValueError("chunk is not assigned to this worker")
            row_data = dict(row)

        expected_len = int(row_data["range_end"]) - int(row_data["range_start"]) + 1
        if len(segment) != expected_len:
            raise ValueError("segment length does not match chunk range")
        if self.verify_chunks:
            expected = calculate_pi_segment(int(row_data["range_start"]), int(row_data["range_end"]), row_data["algorithm"])
            if segment != expected.upper():
                with self.db._lock, self.db.connect() as connection:
                    connection.execute(
                        """
                        UPDATE pool_chunks
                        SET status = 'pending', worker_id = NULL, assigned_at = NULL
                        WHERE chunk_id = ?
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
                SET status = 'completed', segment = ?, compute_ms = ?, submitted_at = ?
                WHERE chunk_id = ? AND status = 'assigned' AND worker_id = ?
                """,
                (segment, max(0, int(compute_ms or 0)), now, chunk_id, worker_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("chunk assignment changed before submission completed")
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
        return {"status": "accepted", "chunk_id": chunk_id, "units": units, "checked_at": utc_now()}

    def ensure_active_task(self) -> None:
        with self.db._lock, self.db.connect() as connection:
            active = connection.execute(
                "SELECT 1 FROM pool_tasks WHERE status IN ('active', 'gathering', 'submitting') LIMIT 1"
            ).fetchone()
            if active is not None:
                return

        try:
            task = get_task_for_identity(self.server_url, self.identity)
        except requests.HTTPError as exc:
            self.db.event("warning", "mainnet task request failed", {"error": str(exc)})
            return
        except requests.RequestException as exc:
            self.db.event("warning", "mainnet task request network error", {"error": str(exc)})
            return

        if task.get("status") != "assigned":
            self.db.event("info", "mainnet did not assign pool work", {"status": task.get("status")})
            return

        pool_task_id = f"pooltask_{uuid.uuid4().hex[:16]}"
        chunks = split_range(int(task["range_start"]), int(task["range_end"]), self.chunk_size)
        now = utc_now()
        with self.db._lock, self.db.connect() as connection:
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
            {"mainnet_task_id": task["task_id"], "chunks": len(chunks), "pool_task_id": pool_task_id},
        )

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

    def finalize_task(self, pool_task: dict[str, Any]) -> None:
        pool_task_id = pool_task["pool_task_id"]
        task = json.loads(pool_task["raw_task_json"])
        started = now_perf()
        try:
            with self.db._lock, self.db.connect() as connection:
                connection.execute(
                    "UPDATE pool_tasks SET status = 'submitting' WHERE pool_task_id = ?",
                    (pool_task_id,),
                )
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
            status = "submitted" if credited else "rejected"
            if reveal.get("accepted"):
                status = "accepted"
            elif reveal.get("status") == "validation_pending":
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
            with self.db._lock, self.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE pool_tasks
                    SET status = 'error', error = ?, completed_at = ?
                    WHERE pool_task_id = ?
                    """,
                    (str(exc), utc_now(), pool_task_id),
                )
            self.db.event("error", "pool task failed", {"pool_task_id": pool_task_id, "error": str(exc)})

    def stats(self) -> dict[str, Any]:
        with self.db._lock, self.db.connect() as connection:
            workers = connection.execute("SELECT COUNT(*) AS count FROM pool_workers").fetchone()["count"]
            tasks = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM pool_tasks
                    GROUP BY status
                    ORDER BY status
                    """
                ).fetchall()
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
                    SELECT worker_id, units
                    FROM pool_shares
                    WHERE credited = 1
                    """
                ).fetchall()
            ]
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
        return {
            "status": "ok",
            "miner_id": self.identity.get("miner_id"),
            "mainnet_server": self.server_url,
            "workers": workers,
            "tasks": tasks,
            "chunks": chunks,
            "credited_shares": summarize_shares(share_rows),
            "events": recent_events,
            "checked_at": utc_now(),
        }


class PoolHandler(BaseHTTPRequestHandler):
    coordinator: PoolCoordinator
    auth_token: str | None = None

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
            elif parsed.path == "/work/next":
                self.require_auth()
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
                self.require_auth()
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
    parser.add_argument("--chunk-size", type=int, default=2)
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--chunk-timeout-seconds", type=int, default=45)
    parser.add_argument("--pool-name", default="picoin-pool")
    parser.add_argument("--auth-token", default=os.getenv("PICOIN_POOL_TOKEN", ""))
    parser.add_argument("--trust-workers", action="store_true", help="Skip pool-side chunk verification")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    identity = load_or_register_identity(args.server.rstrip("/"), args.identity, default_name=args.pool_name)
    db = PoolDatabase(args.db)
    coordinator = PoolCoordinator(
        db=db,
        server_url=args.server,
        identity=identity,
        chunk_size=args.chunk_size,
        poll_seconds=args.poll_seconds,
        chunk_timeout_seconds=args.chunk_timeout_seconds,
        verify_chunks=not args.trust_workers,
    )
    coordinator.start()
    PoolHandler.coordinator = coordinator
    PoolHandler.auth_token = args.auth_token.strip() or None
    httpd = ThreadingHTTPServer((args.host, args.port), PoolHandler)
    print(f"Picoin pool server listening on http://{args.host}:{args.port}")
    print(f"Pool miner identity: {identity.get('miner_id')}")
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
