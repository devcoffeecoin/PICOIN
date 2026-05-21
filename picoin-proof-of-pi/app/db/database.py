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
    GENESIS_ALLOCATIONS_FILE,
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
from app.core.money import to_units
from app.services.genesis import load_genesis_allocations


class PicoinConnection(sqlite3.Connection):
    """SQLite connection that closes when used as a context manager."""

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool | None:
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def _open_connection(db_path: Path = DATABASE_PATH) -> PicoinConnection:
    connection = sqlite3.connect(
        db_path,
        timeout=30,
        check_same_thread=False,
        factory=PicoinConnection,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _open_connection(DATABASE_PATH)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


TASK_COLUMNS = (
    "task_id",
    "miner_id",
    "range_start",
    "range_end",
    "algorithm",
    "status",
    "assignment_seed",
    "assignment_mode",
    "assignment_ms",
    "compute_ms",
    "protocol_params_id",
    "created_at",
    "expires_at",
    "submitted_at",
)


TASKS_TABLE_SQL = """
CREATE TABLE tasks (
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
    FOREIGN KEY(miner_id) REFERENCES miners(miner_id),
    FOREIGN KEY(protocol_params_id) REFERENCES protocol_params(id)
)
"""


def init_db(db_path: Path = DATABASE_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _open_connection(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS miners (
                miner_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                public_key TEXT,
                reward_address TEXT,
                registered_at TEXT NOT NULL,
                trust_score REAL NOT NULL DEFAULT 1.0,
                cooldown_until TEXT,
                is_banned INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS validators (
                validator_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                public_key TEXT NOT NULL,
                reward_address TEXT,
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
                mempool_snapshot_id TEXT,
                selected_tx_hashes TEXT NOT NULL DEFAULT '[]',
                tx_merkle_root TEXT NOT NULL DEFAULT '',
                tx_count INTEGER NOT NULL DEFAULT 0,
                tx_fee_total_units INTEGER NOT NULL DEFAULT 0,
                selected_tx_hashes_hash TEXT,
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
                reward_units INTEGER NOT NULL DEFAULT 0,
                tx_merkle_root TEXT,
                tx_count INTEGER NOT NULL DEFAULT 0,
                tx_hashes TEXT NOT NULL DEFAULT '[]',
                fee_reward REAL NOT NULL DEFAULT 0,
                fee_reward_units INTEGER NOT NULL DEFAULT 0,
                miner_reward_address TEXT,
                state_root TEXT,
                difficulty REAL,
                task_id TEXT NOT NULL UNIQUE,
                protocol_params_id INTEGER,
                protocol_version TEXT NOT NULL DEFAULT '0.18',
                validation_mode TEXT NOT NULL DEFAULT 'external_commit_reveal',
                total_task_ms INTEGER,
                total_block_ms INTEGER,
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
                tx_merkle_root TEXT NOT NULL DEFAULT '',
                mempool_snapshot_id TEXT,
                selected_tx_hashes_hash TEXT,
                tx_count INTEGER NOT NULL DEFAULT 0,
                tx_fee_total_units INTEGER NOT NULL DEFAULT 0,
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
                tx_merkle_root TEXT NOT NULL DEFAULT '',
                mempool_snapshot_id TEXT,
                selected_tx_hashes_hash TEXT,
                tx_count INTEGER NOT NULL DEFAULT 0,
                tx_fee_total_units INTEGER NOT NULL DEFAULT 0,
                tx_hashes_json TEXT NOT NULL DEFAULT '[]',
                transactions_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL,
                assigned_validator_id TEXT,
                result_reason TEXT,
                validator_signature TEXT,
                validation_ms INTEGER,
                job_created_at TEXT,
                first_vote_at TEXT,
                second_vote_at TEXT,
                quorum_reached_at TEXT,
                finalized_at TEXT,
                waiting_for_first_vote_ms INTEGER,
                waiting_for_quorum_ms INTEGER,
                finalization_ms INTEGER,
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
                submit_result_latency_ms INTEGER,
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
                amount_units INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(miner_id) REFERENCES miners(miner_id),
                FOREIGN KEY(block_height) REFERENCES blocks(height)
            );

            CREATE TABLE IF NOT EXISTS balances (
                account_id TEXT PRIMARY KEY,
                account_type TEXT NOT NULL,
                balance REAL NOT NULL DEFAULT 0,
                balance_units INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS account_nonces (
                account_id TEXT PRIMARY KEY,
                nonce INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ledger_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                account_type TEXT NOT NULL,
                amount REAL NOT NULL,
                amount_units INTEGER NOT NULL DEFAULT 0,
                balance_after REAL NOT NULL,
                balance_after_units INTEGER NOT NULL DEFAULT 0,
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
                amount_units INTEGER NOT NULL DEFAULT 0,
                nonce INTEGER NOT NULL,
                fee REAL NOT NULL DEFAULT 0,
                fee_units INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL,
                public_key TEXT NOT NULL,
                signature TEXT NOT NULL,
                status TEXT NOT NULL,
                selected_task_id TEXT,
                selected_block_height INTEGER,
                mempool_snapshot_id TEXT,
                selected_at TEXT,
                confirmed_at TEXT,
                released_at TEXT,
                failure_reason TEXT,
                propagated INTEGER NOT NULL DEFAULT 0,
                block_height INTEGER,
                rejection_reason TEXT,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(sender, nonce)
            );

            CREATE TABLE IF NOT EXISTS task_tx_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                block_height INTEGER NOT NULL,
                tx_hashes_json TEXT NOT NULL,
                tx_merkle_root TEXT NOT NULL,
                tx_count INTEGER NOT NULL,
                tx_fee_total_units INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
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

            CREATE TABLE IF NOT EXISTS canonical_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                height INTEGER NOT NULL UNIQUE,
                block_hash TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                state_root TEXT NOT NULL,
                balances_hash TEXT NOT NULL,
                snapshot_hash TEXT NOT NULL UNIQUE,
                balances_count INTEGER NOT NULL,
                ledger_entries_count INTEGER NOT NULL,
                total_balance REAL NOT NULL DEFAULT 0,
                total_balance_units INTEGER NOT NULL DEFAULT 0,
                trusted INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'local',
                created_at TEXT NOT NULL,
                verified_at TEXT,
                payload TEXT NOT NULL,
                FOREIGN KEY(height) REFERENCES blocks(height)
            );

            CREATE TABLE IF NOT EXISTS canonical_snapshot_imports (
                import_id TEXT PRIMARY KEY,
                height INTEGER NOT NULL,
                block_hash TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                state_root TEXT NOT NULL,
                balances_hash TEXT NOT NULL,
                snapshot_hash TEXT NOT NULL UNIQUE,
                balances_count INTEGER NOT NULL,
                total_balance REAL NOT NULL DEFAULT 0,
                total_balance_units INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'import',
                active INTEGER NOT NULL DEFAULT 0,
                activated_at TEXT,
                state_applied INTEGER NOT NULL DEFAULT 0,
                state_applied_at TEXT,
                imported_at TEXT NOT NULL,
                verified_at TEXT NOT NULL,
                payload TEXT NOT NULL
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
            CREATE INDEX IF NOT EXISTS idx_mempool_selected_task ON mempool_transactions(selected_task_id);
            CREATE INDEX IF NOT EXISTS idx_task_tx_snapshots_task ON task_tx_snapshots(task_id);
            CREATE INDEX IF NOT EXISTS idx_network_block_headers_height ON network_block_headers(height);
            CREATE INDEX IF NOT EXISTS idx_consensus_block_proposals_status ON consensus_block_proposals(status);
            CREATE INDEX IF NOT EXISTS idx_consensus_block_proposals_height ON consensus_block_proposals(height);
            CREATE INDEX IF NOT EXISTS idx_consensus_votes_proposal ON consensus_votes(proposal_id);
            CREATE INDEX IF NOT EXISTS idx_canonical_checkpoints_height ON canonical_checkpoints(height);
            CREATE INDEX IF NOT EXISTS idx_canonical_snapshot_imports_height ON canonical_snapshot_imports(height);
            """
        )
        _ensure_column(connection, "miners", "trust_score", "REAL NOT NULL DEFAULT 1.0")
        _ensure_column(connection, "miners", "cooldown_until", "TEXT")
        _ensure_column(connection, "miners", "is_banned", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "miners", "reward_address", "TEXT")
        _ensure_column(connection, "miners", "last_seen_at", "TEXT")
        _ensure_column(connection, "miners", "last_heartbeat_at", "TEXT")
        _ensure_column(connection, "miners", "online_status", "TEXT NOT NULL DEFAULT 'offline'")
        _ensure_column(connection, "miners", "node_id", "TEXT")
        _ensure_column(connection, "miners", "advertised_address", "TEXT")
        _ensure_column(connection, "miners", "last_ip", "TEXT")
        _ensure_column(connection, "miners", "last_task_id", "TEXT")
        _ensure_column(connection, "miners", "last_task_status", "TEXT")
        _ensure_column(connection, "miners", "last_compute_ms", "INTEGER")
        _ensure_column(connection, "miners", "protocol_version", f"TEXT NOT NULL DEFAULT '{PROTOCOL_VERSION}'")
        _ensure_column(connection, "miners", "enabled", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(connection, "validators", "invalid_results", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "validators", "trust_score", "REAL NOT NULL DEFAULT 1.0")
        _ensure_column(connection, "validators", "cooldown_until", "TEXT")
        _ensure_column(connection, "validators", "last_seen_at", "TEXT")
        _ensure_column(connection, "validators", "last_heartbeat_at", "TEXT")
        _ensure_column(connection, "validators", "online_status", "TEXT NOT NULL DEFAULT 'offline'")
        _ensure_column(connection, "validators", "sync_status", "TEXT NOT NULL DEFAULT 'unknown'")
        _ensure_column(connection, "validators", "out_of_sync_since", "TEXT")
        _ensure_column(connection, "validators", "node_id", "TEXT")
        _ensure_column(connection, "validators", "advertised_address", "TEXT")
        _ensure_column(connection, "validators", "last_ip", "TEXT")
        _ensure_column(connection, "validators", "effective_height", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "validators", "sync_lag", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "validators", "pending_replay_blocks", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "validators", "protocol_version", f"TEXT NOT NULL DEFAULT '{PROTOCOL_VERSION}'")
        _ensure_column(connection, "validators", "reason_if_not_eligible", "TEXT")
        _ensure_column(connection, "validators", "enabled", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(connection, "validators", "total_validation_ms", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "validators", "stake_locked", f"REAL NOT NULL DEFAULT {MIN_VALIDATOR_STAKE}")
        _ensure_column(connection, "validators", "slashed_amount", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "validators", "reward_address", "TEXT")
        _ensure_column(connection, "tasks", "expires_at", "TEXT")
        _ensure_column(connection, "tasks", "assignment_seed", "TEXT")
        _ensure_column(connection, "tasks", "assignment_mode", "TEXT")
        _ensure_column(connection, "tasks", "assignment_ms", "INTEGER")
        _ensure_column(connection, "tasks", "compute_ms", "INTEGER")
        _ensure_column(connection, "tasks", "protocol_params_id", "INTEGER")
        _ensure_tasks_range_constraints(connection)
        _ensure_column(connection, "blocks", "merkle_root", "TEXT")
        _ensure_column(connection, "blocks", "tx_merkle_root", "TEXT")
        _ensure_column(connection, "blocks", "tx_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "blocks", "tx_hashes", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(connection, "blocks", "fee_reward", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "blocks", "reward_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "blocks", "fee_reward_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "blocks", "miner_reward_address", "TEXT")
        _ensure_column(connection, "blocks", "state_root", "TEXT")
        _ensure_column(connection, "blocks", "difficulty", "REAL")
        _ensure_column(connection, "blocks", "protocol_params_id", "INTEGER")
        _ensure_column(connection, "blocks", "total_task_ms", "INTEGER")
        _ensure_column(connection, "blocks", "total_block_ms", "INTEGER")
        _ensure_column(connection, "blocks", "validation_ms", "INTEGER")
        _ensure_column(connection, "blocks", "protocol_version", "TEXT NOT NULL DEFAULT '0.18'")
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
        _ensure_column(connection, "mempool_transactions", "amount_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "mempool_transactions", "fee_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "mempool_transactions", "selected_task_id", "TEXT")
        _ensure_column(connection, "mempool_transactions", "selected_block_height", "INTEGER")
        _ensure_column(connection, "mempool_transactions", "mempool_snapshot_id", "TEXT")
        _ensure_column(connection, "mempool_transactions", "selected_at", "TEXT")
        _ensure_column(connection, "mempool_transactions", "confirmed_at", "TEXT")
        _ensure_column(connection, "mempool_transactions", "released_at", "TEXT")
        _ensure_column(connection, "mempool_transactions", "failure_reason", "TEXT")
        _ensure_column(connection, "tasks", "mempool_snapshot_id", "TEXT")
        _ensure_column(connection, "tasks", "selected_tx_hashes", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(connection, "tasks", "tx_merkle_root", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "tasks", "tx_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "tasks", "tx_fee_total_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "tasks", "selected_tx_hashes_hash", "TEXT")
        _ensure_column(connection, "commitments", "tx_merkle_root", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "commitments", "mempool_snapshot_id", "TEXT")
        _ensure_column(connection, "commitments", "selected_tx_hashes_hash", "TEXT")
        _ensure_column(connection, "commitments", "tx_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "commitments", "tx_fee_total_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "validation_jobs", "tx_merkle_root", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "validation_jobs", "mempool_snapshot_id", "TEXT")
        _ensure_column(connection, "validation_jobs", "selected_tx_hashes_hash", "TEXT")
        _ensure_column(connection, "validation_jobs", "tx_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "validation_jobs", "tx_fee_total_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "validation_jobs", "tx_hashes_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(connection, "validation_jobs", "transactions_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(connection, "balances", "balance_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "ledger_entries", "amount_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "ledger_entries", "balance_after_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "rewards", "amount_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "blocks", "reward_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "blocks", "fee_reward_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "canonical_checkpoints", "total_balance_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "canonical_snapshot_imports", "total_balance_units", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "canonical_snapshot_imports", "active", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "canonical_snapshot_imports", "activated_at", "TEXT")
        _ensure_column(connection, "canonical_snapshot_imports", "state_applied", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "canonical_snapshot_imports", "state_applied_at", "TEXT")
        _ensure_account_nonces(connection)
        _ensure_science_reserve_governance(connection)
        _ensure_scientific_development_treasury(connection)
        _ensure_network_genesis(connection)
        _ensure_column(connection, "commitments", "commit_ms", "INTEGER")
        _ensure_column(connection, "validation_jobs", "validation_ms", "INTEGER")
        _ensure_column(connection, "validation_jobs", "assigned_at", "TEXT")
        _ensure_column(connection, "validation_jobs", "assignment_failures", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "validation_jobs", "blocking_reason", "TEXT")
        _ensure_column(connection, "validation_jobs", "job_created_at", "TEXT")
        _ensure_column(connection, "validation_jobs", "first_vote_at", "TEXT")
        _ensure_column(connection, "validation_jobs", "second_vote_at", "TEXT")
        _ensure_column(connection, "validation_jobs", "quorum_reached_at", "TEXT")
        _ensure_column(connection, "validation_jobs", "finalized_at", "TEXT")
        _ensure_column(connection, "validation_jobs", "waiting_for_first_vote_ms", "INTEGER")
        _ensure_column(connection, "validation_jobs", "waiting_for_quorum_ms", "INTEGER")
        _ensure_column(connection, "validation_jobs", "finalization_ms", "INTEGER")
        _ensure_column(connection, "validation_votes", "submit_result_latency_ms", "INTEGER")
        connection.execute("UPDATE validation_jobs SET job_created_at = created_at WHERE job_created_at IS NULL")
        _ensure_default_protocol_params(connection)
        _ensure_genesis_balance(connection)
        _ensure_existing_validator_stake_balances(connection)
        _backfill_money_units(connection)
        _backfill_account_nonces(connection)


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _backfill_money_units(connection: sqlite3.Connection) -> None:
    money_columns = {
        "balances": [("balance", "balance_units")],
        "ledger_entries": [("amount", "amount_units"), ("balance_after", "balance_after_units")],
        "rewards": [("amount", "amount_units")],
        "blocks": [("reward", "reward_units"), ("fee_reward", "fee_reward_units")],
        "mempool_transactions": [("amount", "amount_units"), ("fee", "fee_units")],
        "canonical_checkpoints": [("total_balance", "total_balance_units")],
        "canonical_snapshot_imports": [("total_balance", "total_balance_units")],
    }
    for table_name, pairs in money_columns.items():
        columns = [column for pair in pairs for column in pair]
        existing_columns = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info({_quoted_identifier(table_name)})").fetchall()
        }
        if not set(columns).issubset(existing_columns):
            continue
        try:
            rows = connection.execute(
                f"SELECT rowid AS _picoin_rowid, {', '.join(_quoted_identifier(column) for column in columns)} "
                f"FROM {_quoted_identifier(table_name)}"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            row_keys = set(row.keys()) if hasattr(row, "keys") else set()
            if "_picoin_rowid" not in row_keys:
                continue
            updates: list[str] = []
            values: list[int] = []
            for amount_column, units_column in pairs:
                if amount_column not in row_keys or units_column not in row_keys:
                    continue
                try:
                    units_value = int(row[units_column] or 0)
                except (TypeError, ValueError):
                    continue
                if units_value != 0 or row[amount_column] in {None, 0, 0.0, "0"}:
                    continue
                try:
                    amount_units = to_units(row[amount_column])
                except (TypeError, ValueError, ArithmeticError):
                    continue
                updates.append(f"{_quoted_identifier(units_column)} = ?")
                values.append(amount_units)
            if updates:
                try:
                    values.append(int(row["_picoin_rowid"]))
                    connection.execute(
                        f"UPDATE {_quoted_identifier(table_name)} SET {', '.join(updates)} WHERE rowid = ?",
                        tuple(values),
                    )
                except (TypeError, ValueError, sqlite3.Error):
                    continue


def _quoted_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _ensure_account_nonces(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS account_nonces (
            account_id TEXT PRIMARY KEY,
            nonce INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )


def _backfill_account_nonces(connection: sqlite3.Connection) -> None:
    try:
        rows = connection.execute(
            """
            SELECT sender AS account_id, MAX(nonce) AS nonce, MAX(updated_at) AS updated_at
            FROM mempool_transactions
            WHERE status = 'confirmed'
            GROUP BY sender
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return
    timestamp = datetime.now(timezone.utc).isoformat()
    for row in rows:
        account_id = str(row["account_id"] or "").strip()
        if not account_id:
            continue
        try:
            nonce = int(row["nonce"] or 0)
        except (TypeError, ValueError):
            continue
        if nonce <= 0:
            continue
        connection.execute(
            """
            INSERT INTO account_nonces (account_id, nonce, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                nonce = CASE WHEN excluded.nonce > account_nonces.nonce THEN excluded.nonce ELSE account_nonces.nonce END,
                updated_at = excluded.updated_at
            """,
            (account_id, nonce, row["updated_at"] or timestamp),
        )


def _tasks_have_global_range_unique(connection: sqlite3.Connection) -> bool:
    for index in connection.execute("PRAGMA index_list(tasks)").fetchall():
        index_name = index[1]
        is_unique = bool(index[2])
        if not is_unique:
            continue
        columns = [
            row[2]
            for row in connection.execute(
                f"PRAGMA index_info({_quoted_identifier(index_name)})"
            ).fetchall()
        ]
        if columns == ["range_start", "range_end", "algorithm"]:
            return True
    return False


def _ensure_tasks_range_constraints(connection: sqlite3.Connection) -> None:
    if _tasks_have_global_range_unique(connection):
        _rebuild_tasks_without_global_range_unique(connection)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
    connection.execute("DROP INDEX IF EXISTS idx_tasks_active_range_unique")
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_protected_range_start_unique
        ON tasks(range_start, algorithm)
        WHERE status IN ('assigned', 'committed', 'revealed', 'accepted')
        """
    )


def _rebuild_tasks_without_global_range_unique(connection: sqlite3.Connection) -> None:
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("DROP TABLE IF EXISTS tasks_without_global_range_unique")
        connection.execute(
            TASKS_TABLE_SQL.replace(
                "CREATE TABLE tasks",
                "CREATE TABLE tasks_without_global_range_unique",
                1,
            )
        )
        existing_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        copy_columns = [column for column in TASK_COLUMNS if column in existing_columns]
        column_list = ", ".join(_quoted_identifier(column) for column in copy_columns)
        connection.execute(
            f"""
            INSERT INTO tasks_without_global_range_unique ({column_list})
            SELECT {column_list}
            FROM tasks
            """
        )
        connection.execute("DROP TABLE tasks")
        connection.execute("ALTER TABLE tasks_without_global_range_unique RENAME TO tasks")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


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
    active_snapshot = connection.execute(
        """
        SELECT 1
        FROM canonical_snapshot_imports
        WHERE active = 1 AND state_applied = 1
        LIMIT 1
        """
    ).fetchone()
    if active_snapshot is not None:
        connection.execute(
            "DELETE FROM ledger_entries WHERE entry_type = 'genesis' AND account_id = ?",
            (GENESIS_ACCOUNT_ID,),
        )
        return
    existing = connection.execute(
        "SELECT 1 FROM ledger_entries WHERE entry_type = 'genesis' AND account_id = ? LIMIT 1",
        (GENESIS_ACCOUNT_ID,),
    ).fetchone()
    if existing is not None:
        _ensure_genesis_allocations(connection)
        return
    timestamp = "1970-01-01T00:00:00+00:00"
    genesis_units = to_units(GENESIS_SUPPLY)
    connection.execute(
        """
        INSERT INTO balances (account_id, account_type, balance, balance_units, updated_at)
        VALUES (?, 'genesis', ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            account_type = excluded.account_type,
            balance = excluded.balance,
            balance_units = excluded.balance_units,
            updated_at = excluded.updated_at
        """,
        (GENESIS_ACCOUNT_ID, GENESIS_SUPPLY, genesis_units, timestamp),
    )
    connection.execute(
        """
        INSERT INTO ledger_entries (
            account_id, account_type, amount, amount_units, balance_after, balance_after_units, entry_type,
            block_height, related_id, description, created_at
        )
        VALUES (?, 'genesis', ?, ?, ?, ?, 'genesis', 0, 'genesis', 'genesis allocation', ?)
        """,
        (GENESIS_ACCOUNT_ID, GENESIS_SUPPLY, genesis_units, GENESIS_SUPPLY, genesis_units, timestamp),
    )
    _ensure_genesis_allocations(connection)


def _ensure_genesis_allocations(connection: sqlite3.Connection) -> None:
    if not GENESIS_ALLOCATIONS_FILE:
        return
    allocation_path = Path(GENESIS_ALLOCATIONS_FILE)
    if not allocation_path.is_absolute():
        allocation_path = DATA_DIR.parent / allocation_path
    document = load_genesis_allocations(allocation_path)
    if document is None:
        return
    if document.get("network_id") and document["network_id"] != NETWORK_ID:
        raise RuntimeError("genesis allocations network_id mismatch")
    if document.get("chain_id") and document["chain_id"] != CHAIN_ID:
        raise RuntimeError("genesis allocations chain_id mismatch")
    timestamp = document["created_at"]
    for allocation in document["allocations"]:
        account_id = allocation["account_id"]
        existing = connection.execute(
            """
            SELECT 1
            FROM ledger_entries
            WHERE account_id = ? AND entry_type = 'genesis_allocation'
            LIMIT 1
            """,
            (account_id,),
        ).fetchone()
        if existing is not None:
            continue
        amount = round(float(allocation["amount"]), 8)
        amount_units = to_units(allocation["amount"])
        genesis = connection.execute(
            "SELECT balance, balance_units FROM balances WHERE account_id = ?",
            (GENESIS_ACCOUNT_ID,),
        ).fetchone()
        genesis_units = 0 if genesis is None else int(genesis["balance_units"] if isinstance(genesis, sqlite3.Row) else genesis[1])
        if genesis_units < amount_units:
            raise RuntimeError(f"genesis balance insufficient for allocation to {account_id}")
        genesis_after_units = genesis_units - amount_units
        genesis_after = round(genesis_after_units / 1_000_000, 8)
        connection.execute(
            "UPDATE balances SET balance = ?, balance_units = ?, updated_at = ? WHERE account_id = ?",
            (genesis_after, genesis_after_units, timestamp, GENESIS_ACCOUNT_ID),
        )
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, amount_units, balance_after, balance_after_units, entry_type,
                block_height, related_id, description, created_at
            )
            VALUES (?, 'genesis', ?, ?, ?, ?, 'genesis_allocation_debit', NULL, ?, ?, ?)
            """,
            (
                GENESIS_ACCOUNT_ID,
                -amount,
                -amount_units,
                genesis_after,
                genesis_after_units,
                account_id,
                f"genesis allocation debit for {account_id}",
                timestamp,
            ),
        )
        balance_row = connection.execute(
            "SELECT balance, balance_units FROM balances WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        current_units = 0 if balance_row is None else int(
            balance_row["balance_units"] if isinstance(balance_row, sqlite3.Row) else balance_row[1]
        )
        next_units = current_units + amount_units
        next_balance = round(next_units / 1_000_000, 8)
        connection.execute(
            """
            INSERT INTO balances (account_id, account_type, balance, balance_units, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                account_type = excluded.account_type,
                balance = excluded.balance,
                balance_units = excluded.balance_units,
                updated_at = excluded.updated_at
            """,
            (account_id, allocation["account_type"], next_balance, next_units, timestamp),
        )
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, amount_units, balance_after, balance_after_units, entry_type,
                block_height, related_id, description, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'genesis_allocation', NULL, 'genesis', ?, ?)
            """,
            (
                account_id,
                allocation["account_type"],
                amount,
                amount_units,
                next_balance,
                next_units,
                allocation["description"],
                timestamp,
            ),
        )


def _ensure_existing_validator_stake_balances(connection: sqlite3.Connection) -> None:
    return
