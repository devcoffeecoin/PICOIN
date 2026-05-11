import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.settings import (
    DATA_DIR,
    DATABASE_PATH,
    DEFAULT_REWARD,
    GENESIS_ACCOUNT_ID,
    GENESIS_SUPPLY,
    MAX_ACTIVE_TASKS_PER_MINER,
    MAX_PI_POSITION,
    MIN_VALIDATOR_STAKE,
    PI_ALGORITHM,
    PROTOCOL_VERSION,
    RANGE_ASSIGNMENT_MAX_ATTEMPTS,
    RANGE_ASSIGNMENT_MODE,
    REQUIRED_VALIDATOR_APPROVALS,
    SAMPLE_COUNT,
    TASK_EXPIRATION_SECONDS,
    TASK_SEGMENT_SIZE,
    VALIDATION_MODE,
)


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

            CREATE TABLE IF NOT EXISTS validators (
                validator_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                public_key TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                accepted_jobs INTEGER NOT NULL DEFAULT 0,
                rejected_jobs INTEGER NOT NULL DEFAULT 0,
                invalid_results INTEGER NOT NULL DEFAULT 0,
                trust_score REAL NOT NULL DEFAULT 1.0,
                cooldown_until TEXT,
                last_seen_at TEXT,
                total_validation_ms INTEGER NOT NULL DEFAULT 0,
                stake_locked REAL NOT NULL DEFAULT 31.416,
                slashed_amount REAL NOT NULL DEFAULT 0,
                is_banned INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS protocol_params (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                protocol_version TEXT NOT NULL,
                algorithm TEXT NOT NULL,
                validation_mode TEXT NOT NULL,
                required_validator_approvals INTEGER NOT NULL,
                range_assignment_mode TEXT NOT NULL,
                max_pi_position INTEGER NOT NULL,
                range_assignment_max_attempts INTEGER NOT NULL,
                segment_size INTEGER NOT NULL,
                sample_count INTEGER NOT NULL,
                task_expiration_seconds INTEGER NOT NULL,
                max_active_tasks_per_miner INTEGER NOT NULL,
                base_reward REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS retarget_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                previous_protocol_params_id INTEGER,
                new_protocol_params_id INTEGER,
                epoch_start_height INTEGER NOT NULL,
                epoch_end_height INTEGER NOT NULL,
                epoch_block_count INTEGER NOT NULL,
                average_block_ms REAL NOT NULL,
                target_block_ms INTEGER NOT NULL,
                old_difficulty REAL NOT NULL,
                new_difficulty REAL NOT NULL,
                adjustment_factor REAL NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(previous_protocol_params_id) REFERENCES protocol_params(id),
                FOREIGN KEY(new_protocol_params_id) REFERENCES protocol_params(id)
            );

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                miner_id TEXT NOT NULL,
                range_start INTEGER NOT NULL,
                range_end INTEGER NOT NULL,
                algorithm TEXT NOT NULL,
                status TEXT NOT NULL,
                assignment_seed TEXT,
                assignment_mode TEXT,
                assignment_ms INTEGER,
                compute_ms INTEGER,
                protocol_params_id INTEGER,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                submitted_at TEXT,
                UNIQUE(range_start, range_end, algorithm),
                FOREIGN KEY(miner_id) REFERENCES miners(miner_id),
                FOREIGN KEY(protocol_params_id) REFERENCES protocol_params(id)
            );

            CREATE TABLE IF NOT EXISTS blocks (
                height INTEGER PRIMARY KEY,
                previous_hash TEXT NOT NULL,
                miner_id TEXT NOT NULL,
                range_start INTEGER NOT NULL,
                range_end INTEGER NOT NULL,
                algorithm TEXT NOT NULL,
                result_hash TEXT NOT NULL UNIQUE,
                merkle_root TEXT,
                samples TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                block_hash TEXT NOT NULL UNIQUE,
                reward REAL NOT NULL,
                difficulty REAL,
                task_id TEXT NOT NULL UNIQUE,
                protocol_params_id INTEGER,
                protocol_version TEXT NOT NULL DEFAULT '0.15',
                validation_mode TEXT NOT NULL DEFAULT 'external_commit_reveal',
                total_task_ms INTEGER,
                validation_ms INTEGER,
                FOREIGN KEY(miner_id) REFERENCES miners(miner_id),
                FOREIGN KEY(task_id) REFERENCES tasks(task_id),
                FOREIGN KEY(protocol_params_id) REFERENCES protocol_params(id)
            );

            CREATE TABLE IF NOT EXISTS commitments (
                task_id TEXT PRIMARY KEY,
                miner_id TEXT NOT NULL,
                result_hash TEXT NOT NULL,
                merkle_root TEXT NOT NULL,
                challenge_seed TEXT NOT NULL,
                samples TEXT NOT NULL,
                signature TEXT NOT NULL,
                signed_at TEXT NOT NULL,
                commit_ms INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(miner_id) REFERENCES miners(miner_id),
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
            );

            CREATE TABLE IF NOT EXISTS validation_jobs (
                job_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                miner_id TEXT NOT NULL,
                result_hash TEXT NOT NULL,
                merkle_root TEXT NOT NULL,
                challenge_seed TEXT NOT NULL,
                samples TEXT NOT NULL,
                status TEXT NOT NULL,
                assigned_validator_id TEXT,
                result_reason TEXT,
                validator_signature TEXT,
                validation_ms INTEGER,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(miner_id) REFERENCES miners(miner_id),
                FOREIGN KEY(task_id) REFERENCES tasks(task_id),
                FOREIGN KEY(assigned_validator_id) REFERENCES validators(validator_id)
            );

            CREATE TABLE IF NOT EXISTS validation_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                validator_id TEXT NOT NULL,
                approved INTEGER NOT NULL,
                reason TEXT NOT NULL,
                signature TEXT NOT NULL,
                signed_at TEXT NOT NULL,
                validation_ms INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(job_id, validator_id),
                FOREIGN KEY(job_id) REFERENCES validation_jobs(job_id),
                FOREIGN KEY(task_id) REFERENCES tasks(task_id),
                FOREIGN KEY(validator_id) REFERENCES validators(validator_id)
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

            CREATE TABLE IF NOT EXISTS balances (
                account_id TEXT PRIMARY KEY,
                account_type TEXT NOT NULL,
                balance REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ledger_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                account_type TEXT NOT NULL,
                amount REAL NOT NULL,
                balance_after REAL NOT NULL,
                entry_type TEXT NOT NULL,
                block_height INTEGER,
                related_id TEXT,
                description TEXT,
                created_at TEXT NOT NULL
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
            CREATE INDEX IF NOT EXISTS idx_commitments_miner ON commitments(miner_id);
            CREATE INDEX IF NOT EXISTS idx_validation_jobs_status ON validation_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_validation_votes_job ON validation_votes(job_id);
            CREATE INDEX IF NOT EXISTS idx_submissions_miner ON submissions(miner_id);
            CREATE INDEX IF NOT EXISTS idx_penalties_miner ON penalties(miner_id);
            CREATE INDEX IF NOT EXISTS idx_ledger_account ON ledger_entries(account_id);
            CREATE INDEX IF NOT EXISTS idx_ledger_type ON ledger_entries(entry_type);
            """
        )
        _ensure_column(connection, "miners", "trust_score", "REAL NOT NULL DEFAULT 1.0")
        _ensure_column(connection, "miners", "cooldown_until", "TEXT")
        _ensure_column(connection, "miners", "is_banned", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "validators", "invalid_results", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "validators", "trust_score", "REAL NOT NULL DEFAULT 1.0")
        _ensure_column(connection, "validators", "cooldown_until", "TEXT")
        _ensure_column(connection, "validators", "last_seen_at", "TEXT")
        _ensure_column(connection, "validators", "total_validation_ms", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "validators", "stake_locked", f"REAL NOT NULL DEFAULT {MIN_VALIDATOR_STAKE}")
        _ensure_column(connection, "validators", "slashed_amount", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "tasks", "expires_at", "TEXT")
        _ensure_column(connection, "tasks", "assignment_seed", "TEXT")
        _ensure_column(connection, "tasks", "assignment_mode", "TEXT")
        _ensure_column(connection, "tasks", "assignment_ms", "INTEGER")
        _ensure_column(connection, "tasks", "compute_ms", "INTEGER")
        _ensure_column(connection, "tasks", "protocol_params_id", "INTEGER")
        _ensure_column(connection, "blocks", "merkle_root", "TEXT")
        _ensure_column(connection, "blocks", "difficulty", "REAL")
        _ensure_column(connection, "blocks", "protocol_params_id", "INTEGER")
        _ensure_column(connection, "blocks", "total_task_ms", "INTEGER")
        _ensure_column(connection, "blocks", "validation_ms", "INTEGER")
        _ensure_column(connection, "blocks", "protocol_version", "TEXT NOT NULL DEFAULT '0.15'")
        _ensure_column(connection, "blocks", "validation_mode", "TEXT NOT NULL DEFAULT 'external_commit_reveal'")
        _ensure_column(connection, "commitments", "commit_ms", "INTEGER")
        _ensure_column(connection, "validation_jobs", "validation_ms", "INTEGER")
        _ensure_default_protocol_params(connection)
        _ensure_genesis_balance(connection)
        _ensure_existing_validator_stake_balances(connection)


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _ensure_default_protocol_params(connection: sqlite3.Connection) -> None:
    active = connection.execute(
        """
        SELECT protocol_version, algorithm, validation_mode, required_validator_approvals,
               range_assignment_mode, max_pi_position, range_assignment_max_attempts,
               segment_size, sample_count, task_expiration_seconds,
               max_active_tasks_per_miner, base_reward
        FROM protocol_params
        WHERE active = 1
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    defaults = (
        PROTOCOL_VERSION,
        PI_ALGORITHM,
        VALIDATION_MODE,
        REQUIRED_VALIDATOR_APPROVALS,
        RANGE_ASSIGNMENT_MODE,
        MAX_PI_POSITION,
        RANGE_ASSIGNMENT_MAX_ATTEMPTS,
        TASK_SEGMENT_SIZE,
        SAMPLE_COUNT,
        TASK_EXPIRATION_SECONDS,
        MAX_ACTIVE_TASKS_PER_MINER,
        DEFAULT_REWARD,
    )
    if active is not None and tuple(active) == defaults:
        return
    connection.execute("UPDATE protocol_params SET active = 0 WHERE active = 1")
    connection.execute(
        """
        INSERT INTO protocol_params (
            protocol_version, algorithm, validation_mode, required_validator_approvals,
            range_assignment_mode, max_pi_position, range_assignment_max_attempts,
            segment_size, sample_count, task_expiration_seconds,
            max_active_tasks_per_miner, base_reward, active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            PROTOCOL_VERSION,
            PI_ALGORITHM,
            VALIDATION_MODE,
            REQUIRED_VALIDATOR_APPROVALS,
            RANGE_ASSIGNMENT_MODE,
            MAX_PI_POSITION,
            RANGE_ASSIGNMENT_MAX_ATTEMPTS,
            TASK_SEGMENT_SIZE,
            SAMPLE_COUNT,
            TASK_EXPIRATION_SECONDS,
            MAX_ACTIVE_TASKS_PER_MINER,
            DEFAULT_REWARD,
        ),
    )


def _ensure_genesis_balance(connection: sqlite3.Connection) -> None:
    existing = connection.execute(
        "SELECT 1 FROM ledger_entries WHERE entry_type = 'genesis' AND account_id = ? LIMIT 1",
        (GENESIS_ACCOUNT_ID,),
    ).fetchone()
    if existing is not None:
        return
    timestamp = "1970-01-01T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO balances (account_id, account_type, balance, updated_at)
        VALUES (?, 'genesis', ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            account_type = excluded.account_type,
            balance = excluded.balance,
            updated_at = excluded.updated_at
        """,
        (GENESIS_ACCOUNT_ID, GENESIS_SUPPLY, timestamp),
    )
    connection.execute(
        """
        INSERT INTO ledger_entries (
            account_id, account_type, amount, balance_after, entry_type,
            block_height, related_id, description, created_at
        )
        VALUES (?, 'genesis', ?, ?, 'genesis', 0, 'genesis', 'genesis allocation', ?)
        """,
        (GENESIS_ACCOUNT_ID, GENESIS_SUPPLY, GENESIS_SUPPLY, timestamp),
    )


def _ensure_existing_validator_stake_balances(connection: sqlite3.Connection) -> None:
    validators = connection.execute(
        """
        SELECT validator_id, stake_locked
        FROM validators
        WHERE stake_locked > 0
        AND NOT EXISTS (
            SELECT 1
            FROM ledger_entries
            WHERE ledger_entries.account_id = validators.validator_id
            AND ledger_entries.entry_type = 'validator_stake_lock'
        )
        """
    ).fetchall()
    for validator in validators:
        timestamp = datetime.now(timezone.utc).isoformat()
        validator_id = validator[0]
        stake = round(float(validator[1]), 8)
        genesis_balance = connection.execute(
            "SELECT balance FROM balances WHERE account_id = ?",
            (GENESIS_ACCOUNT_ID,),
        ).fetchone()[0]
        next_genesis_balance = round(float(genesis_balance) - stake, 8)
        connection.execute(
            "UPDATE balances SET balance = ?, updated_at = ? WHERE account_id = ?",
            (next_genesis_balance, timestamp, GENESIS_ACCOUNT_ID),
        )
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, balance_after, entry_type,
                block_height, related_id, description, created_at
            )
            VALUES (?, 'genesis', ?, ?, 'validator_stake_grant', NULL, ?, ?, ?)
            """,
            (
                GENESIS_ACCOUNT_ID,
                -stake,
                next_genesis_balance,
                validator_id,
                "simulated validator stake funded from genesis",
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO balances (account_id, account_type, balance, updated_at)
            VALUES (?, 'validator', ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                account_type = 'validator',
                balance = balances.balance + excluded.balance,
                updated_at = excluded.updated_at
            """,
            (validator_id, stake, timestamp),
        )
        validator_balance = connection.execute(
            "SELECT balance FROM balances WHERE account_id = ?",
            (validator_id,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, balance_after, entry_type,
                block_height, related_id, description, created_at
            )
            VALUES (?, 'validator', ?, ?, 'validator_stake_lock', NULL, ?, ?, ?)
            """,
            (
                validator_id,
                stake,
                validator_balance,
                validator_id,
                "simulated validator stake locked",
                timestamp,
            ),
        )
