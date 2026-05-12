import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.settings import (
    CHAIN_ID,
    DATA_DIR,
    DATABASE_PATH,
    DEFAULT_REWARD,
    GENESIS_ACCOUNT_ID,
    GENESIS_HASH,
    GENESIS_SUPPLY,
    MAX_ACTIVE_TASKS_PER_MINER,
    MAX_PI_POSITION,
    NETWORK_ID,
    MIN_VALIDATOR_STAKE,
    PI_ALGORITHM,
    PROTOCOL_VERSION,
    RANGE_ASSIGNMENT_MAX_ATTEMPTS,
    RANGE_ASSIGNMENT_MODE,
    REQUIRED_VALIDATOR_APPROVALS,
    SAMPLE_COUNT,
    SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET,
    SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
    SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET,
    SCIENCE_RESERVE_AUTHORIZED_SIGNERS,
    SCIENCE_RESERVE_LOCKED_STATUS,
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
                protocol_version TEXT NOT NULL DEFAULT '0.17',
                validation_mode TEXT NOT NULL DEFAULT 'external_commit_reveal',
                total_task_ms INTEGER,
                validation_ms INTEGER,
                fraudulent INTEGER NOT NULL DEFAULT 0,
                fraud_reason TEXT,
                fraud_detected_at TEXT,
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

            CREATE TABLE IF NOT EXISTS retroactive_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                block_height INTEGER NOT NULL,
                block_hash TEXT NOT NULL,
                audit_seed TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                samples TEXT NOT NULL,
                expected_hash TEXT NOT NULL,
                actual_hash TEXT NOT NULL,
                passed INTEGER NOT NULL,
                reason TEXT NOT NULL,
                automatic INTEGER NOT NULL DEFAULT 0,
                reward REAL NOT NULL DEFAULT 0,
                reward_account_id TEXT,
                fraud_detected INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(block_height) REFERENCES blocks(height)
            );

            CREATE TABLE IF NOT EXISTS science_stake_accounts (
                account_id TEXT PRIMARY KEY,
                address TEXT NOT NULL UNIQUE,
                stake_amount REAL NOT NULL,
                tier TEXT,
                compute_multiplier INTEGER NOT NULL DEFAULT 0,
                monthly_quota_used REAL NOT NULL DEFAULT 0,
                monthly_quota_epoch TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS science_jobs (
                job_id TEXT PRIMARY KEY,
                requester_address TEXT NOT NULL,
                tier_at_creation TEXT NOT NULL,
                job_type TEXT NOT NULL,
                metadata_hash TEXT NOT NULL,
                storage_pointer TEXT NOT NULL,
                reward_budget REAL NOT NULL DEFAULT 0,
                max_compute_units REAL NOT NULL DEFAULT 0,
                reward_per_compute_unit REAL NOT NULL DEFAULT 0,
                max_reward REAL NOT NULL DEFAULT 0,
                compute_units_used REAL NOT NULL DEFAULT 0,
                payout_amount REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                worker_address TEXT,
                result_hash TEXT,
                proof_hash TEXT,
                paid INTEGER NOT NULL DEFAULT 0,
                paid_amount REAL NOT NULL DEFAULT 0,
                paid_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(requester_address) REFERENCES science_stake_accounts(address)
            );

            CREATE TABLE IF NOT EXISTS science_reward_reserve (
                epoch TEXT PRIMARY KEY,
                total_reserved REAL NOT NULL DEFAULT 0,
                total_paid REAL NOT NULL DEFAULT 0,
                total_pending REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS science_reserve_governance (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                status TEXT NOT NULL,
                activation_requested_at TEXT,
                activation_available_at TEXT,
                activated_at TEXT,
                approvals TEXT NOT NULL DEFAULT '[]',
                authorized_signers TEXT NOT NULL DEFAULT '[]',
                payouts_enabled INTEGER NOT NULL DEFAULT 0,
                emergency_paused INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scientific_development_treasury (
                treasury_id TEXT PRIMARY KEY,
                total_accumulated REAL NOT NULL DEFAULT 0,
                total_claimed REAL NOT NULL DEFAULT 0,
                locked_balance REAL NOT NULL DEFAULT 0,
                unlocked_balance REAL NOT NULL DEFAULT 0,
                current_epoch TEXT NOT NULL,
                epoch_start_block INTEGER NOT NULL DEFAULT 0,
                epoch_end_block INTEGER NOT NULL DEFAULT 0,
                next_unlock_at TEXT NOT NULL,
                last_claim_at TEXT,
                treasury_wallet TEXT NOT NULL,
                governance_wallet TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scientific_development_treasury_epochs (
                epoch TEXT PRIMARY KEY,
                start_block INTEGER NOT NULL,
                end_block INTEGER NOT NULL,
                locked_amount REAL NOT NULL DEFAULT 0,
                unlocked_amount REAL NOT NULL DEFAULT 0,
                claimed_amount REAL NOT NULL DEFAULT 0,
                unlock_at TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scientific_development_treasury_claims (
                claim_id TEXT PRIMARY KEY,
                amount REAL NOT NULL,
                claim_to TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS science_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                address TEXT,
                job_id TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS network_peers (
                peer_id TEXT PRIMARY KEY,
                node_id TEXT NOT NULL,
                peer_address TEXT NOT NULL UNIQUE,
                peer_type TEXT NOT NULL,
                protocol_version TEXT NOT NULL,
                network_id TEXT NOT NULL,
                chain_id TEXT NOT NULL,
                genesis_hash TEXT NOT NULL,
                connected_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                status TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS mempool_transactions (
                tx_hash TEXT PRIMARY KEY,
                tx_type TEXT NOT NULL,
                sender TEXT NOT NULL,
                recipient TEXT,
                amount REAL NOT NULL DEFAULT 0,
                nonce INTEGER NOT NULL,
                fee REAL NOT NULL DEFAULT 0,
                payload TEXT NOT NULL,
                public_key TEXT NOT NULL,
                signature TEXT NOT NULL,
                status TEXT NOT NULL,
                propagated INTEGER NOT NULL DEFAULT 0,
                block_height INTEGER,
                rejection_reason TEXT,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(sender, nonce)
            );

            CREATE TABLE IF NOT EXISTS network_block_headers (
                block_hash TEXT PRIMARY KEY,
                height INTEGER NOT NULL,
                previous_hash TEXT NOT NULL,
                source_peer_id TEXT,
                status TEXT NOT NULL,
                reason TEXT,
                payload TEXT NOT NULL,
                received_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS network_sync_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id TEXT,
                event_type TEXT NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS consensus_block_proposals (
                proposal_id TEXT PRIMARY KEY,
                block_hash TEXT NOT NULL UNIQUE,
                height INTEGER NOT NULL,
                previous_hash TEXT NOT NULL,
                proposer_node_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                approvals INTEGER NOT NULL DEFAULT 0,
                rejections INTEGER NOT NULL DEFAULT 0,
                rejection_reason TEXT,
                finalized_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS consensus_votes (
                vote_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL,
                block_hash TEXT NOT NULL,
                validator_id TEXT NOT NULL,
                approved INTEGER NOT NULL,
                reason TEXT NOT NULL,
                signature TEXT NOT NULL,
                signed_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(proposal_id, validator_id),
                FOREIGN KEY(proposal_id) REFERENCES consensus_block_proposals(proposal_id)
            );

            CREATE TABLE IF NOT EXISTS consensus_finalizations (
                finalization_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL UNIQUE,
                block_hash TEXT NOT NULL UNIQUE,
                height INTEGER NOT NULL,
                quorum INTEGER NOT NULL,
                approvals INTEGER NOT NULL,
                validator_ids TEXT NOT NULL,
                imported INTEGER NOT NULL DEFAULT 0,
                finalized_at TEXT NOT NULL,
                FOREIGN KEY(proposal_id) REFERENCES consensus_block_proposals(proposal_id)
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
            CREATE INDEX IF NOT EXISTS idx_retroactive_audits_block ON retroactive_audits(block_height);
            CREATE INDEX IF NOT EXISTS idx_science_jobs_requester ON science_jobs(requester_address);
            CREATE INDEX IF NOT EXISTS idx_science_jobs_status ON science_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_science_events_type ON science_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_scientific_treasury_epochs_status ON scientific_development_treasury_epochs(status);
            CREATE INDEX IF NOT EXISTS idx_network_peers_status ON network_peers(status);
            CREATE INDEX IF NOT EXISTS idx_network_peers_last_seen ON network_peers(last_seen);
            CREATE INDEX IF NOT EXISTS idx_mempool_status ON mempool_transactions(status);
            CREATE INDEX IF NOT EXISTS idx_mempool_sender_nonce ON mempool_transactions(sender, nonce);
            CREATE INDEX IF NOT EXISTS idx_network_block_headers_height ON network_block_headers(height);
            CREATE INDEX IF NOT EXISTS idx_consensus_block_proposals_status ON consensus_block_proposals(status);
            CREATE INDEX IF NOT EXISTS idx_consensus_block_proposals_height ON consensus_block_proposals(height);
            CREATE INDEX IF NOT EXISTS idx_consensus_votes_proposal ON consensus_votes(proposal_id);
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
        _ensure_column(connection, "blocks", "protocol_version", "TEXT NOT NULL DEFAULT '0.17'")
        _ensure_column(connection, "blocks", "validation_mode", "TEXT NOT NULL DEFAULT 'external_commit_reveal'")
        _ensure_column(connection, "blocks", "fraudulent", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "blocks", "fraud_reason", "TEXT")
        _ensure_column(connection, "blocks", "fraud_detected_at", "TEXT")
        _ensure_column(connection, "retroactive_audits", "automatic", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "retroactive_audits", "reward", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "retroactive_audits", "reward_account_id", "TEXT")
        _ensure_column(connection, "retroactive_audits", "fraud_detected", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "science_jobs", "paid", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "science_jobs", "paid_amount", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "science_jobs", "paid_at", "TEXT")
        _ensure_column(connection, "science_jobs", "max_compute_units", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "science_jobs", "reward_per_compute_unit", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "science_jobs", "max_reward", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "science_jobs", "compute_units_used", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "science_jobs", "payout_amount", "REAL NOT NULL DEFAULT 0")
        connection.execute(
            """
            UPDATE science_jobs
            SET max_reward = reward_budget,
                max_compute_units = CASE WHEN reward_budget > 0 AND max_compute_units = 0 THEN 1 ELSE max_compute_units END,
                reward_per_compute_unit = CASE WHEN reward_budget > 0 AND reward_per_compute_unit = 0 THEN reward_budget ELSE reward_per_compute_unit END,
                payout_amount = CASE WHEN paid_amount > 0 AND payout_amount = 0 THEN paid_amount ELSE payout_amount END
            WHERE max_reward = 0
            """
        )
        _ensure_column(connection, "science_reserve_governance", "authorized_signers", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(connection, "science_reserve_governance", "payouts_enabled", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "science_reserve_governance", "emergency_paused", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "network_peers", "genesis_hash", f"TEXT NOT NULL DEFAULT '{GENESIS_HASH}'")
        _ensure_column(connection, "mempool_transactions", "expires_at", "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'")
        _ensure_science_reserve_governance(connection)
        _ensure_scientific_development_treasury(connection)
        _ensure_network_genesis(connection)
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


def _ensure_science_reserve_governance(connection: sqlite3.Connection) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    authorized_signers = json.dumps(list(SCIENCE_RESERVE_AUTHORIZED_SIGNERS))
    connection.execute(
        """
        INSERT INTO science_reserve_governance (
            id, status, activation_requested_at, activation_available_at,
            activated_at, approvals, authorized_signers, payouts_enabled,
            emergency_paused, updated_at
        )
        VALUES (1, ?, NULL, NULL, NULL, '[]', ?, 0, 0, ?)
        ON CONFLICT(id) DO UPDATE SET
            authorized_signers = CASE
                WHEN science_reserve_governance.authorized_signers = '[]'
                THEN excluded.authorized_signers
                ELSE science_reserve_governance.authorized_signers
            END,
            updated_at = excluded.updated_at
        """,
        (SCIENCE_RESERVE_LOCKED_STATUS, authorized_signers, timestamp),
    )


def _ensure_scientific_development_treasury(connection: sqlite3.Connection) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        INSERT INTO scientific_development_treasury (
            treasury_id, total_accumulated, total_claimed, locked_balance,
            unlocked_balance, current_epoch, epoch_start_block, epoch_end_block,
            next_unlock_at, last_claim_at, treasury_wallet, governance_wallet,
            created_at, updated_at
        )
        VALUES (?, 0, 0, 0, 0, 'bootstrap', 0, 0, ?, NULL, ?, ?, ?, ?)
        ON CONFLICT(treasury_id) DO UPDATE SET
            treasury_wallet = excluded.treasury_wallet,
            governance_wallet = excluded.governance_wallet,
            updated_at = excluded.updated_at
        """,
        (
            SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
            timestamp,
            SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET,
            SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET,
            timestamp,
            timestamp,
        ),
    )


def _ensure_network_genesis(connection: sqlite3.Connection) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    existing = connection.execute(
        """
        SELECT 1
        FROM network_sync_events
        WHERE event_type = 'network_genesis' AND status = 'active'
        LIMIT 1
        """
    ).fetchone()
    if existing is not None:
        return
    connection.execute(
        """
        INSERT INTO network_sync_events (peer_id, event_type, direction, status, details, created_at)
        VALUES (NULL, 'network_genesis', 'local', 'active', ?, ?)
        """,
        (
            json.dumps(
                {
                    "chain_id": CHAIN_ID,
                    "network_id": NETWORK_ID,
                    "genesis_hash": GENESIS_HASH,
                    "protocol_version": PROTOCOL_VERSION,
                },
                sort_keys=True,
            ),
            timestamp,
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
    return
