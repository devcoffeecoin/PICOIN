from __future__ import annotations

import sqlite3
from pathlib import Path


class MarketplaceStorage:
    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "marketplace.sqlite3"
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
                CREATE TABLE IF NOT EXISTS mining_pools (
                    pool_id TEXT PRIMARY KEY,
                    hardware_type TEXT NOT NULL,
                    paired_coin TEXT NOT NULL,
                    pair_symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_mining_pools_pair_hardware
                    ON mining_pools(hardware_type, paired_coin);

                CREATE INDEX IF NOT EXISTS idx_mining_pools_status
                    ON mining_pools(status, updated_at);

                CREATE TABLE IF NOT EXISTS listings (
                    listing_id TEXT PRIMARY KEY,
                    pool_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    provider_wallet TEXT NOT NULL,
                    hardware_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_listings_hardware_status
                    ON listings(hardware_type, status, updated_at);

                CREATE INDEX IF NOT EXISTS idx_listings_pool_status
                    ON listings(pool_id, status, updated_at);

                CREATE INDEX IF NOT EXISTS idx_listings_provider
                    ON listings(provider_id, updated_at);

                CREATE TABLE IF NOT EXISTS bookings (
                    booking_id TEXT PRIMARY KEY,
                    pool_id TEXT NOT NULL,
                    listing_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    requester_wallet TEXT NOT NULL,
                    hardware_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_bookings_listing_status
                    ON bookings(listing_id, status, updated_at);

                CREATE INDEX IF NOT EXISTS idx_bookings_pool_status
                    ON bookings(pool_id, status, updated_at);

                CREATE INDEX IF NOT EXISTS idx_bookings_requester
                    ON bookings(requester_wallet, updated_at);

                CREATE TABLE IF NOT EXISTS payments (
                    payment_id TEXT PRIMARY KEY,
                    booking_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tx_hash TEXT,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_payments_booking
                    ON payments(booking_id, updated_at);

                CREATE INDEX IF NOT EXISTS idx_payments_tx_hash
                    ON payments(tx_hash);

                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_accounts_status
                    ON accounts(status, updated_at);

                CREATE TABLE IF NOT EXISTS chains (
                    chain_code TEXT PRIMARY KEY,
                    family TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_chains_enabled
                    ON chains(enabled, family);

                CREATE TABLE IF NOT EXISTS tokens (
                    chain_code TEXT NOT NULL,
                    token_symbol TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (chain_code, token_symbol)
                );

                CREATE INDEX IF NOT EXISTS idx_tokens_enabled
                    ON tokens(enabled, chain_code, token_symbol);

                CREATE TABLE IF NOT EXISTS account_wallets (
                    wallet_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    chain_code TEXT NOT NULL,
                    address TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_account_wallets_chain_address
                    ON account_wallets(chain_code, address);

                CREATE INDEX IF NOT EXISTS idx_account_wallets_account
                    ON account_wallets(account_id, status, updated_at);

                CREATE TABLE IF NOT EXISTS deposits (
                    deposit_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    wallet_id TEXT NOT NULL,
                    chain_code TEXT NOT NULL,
                    token_symbol TEXT NOT NULL,
                    tx_hash TEXT NOT NULL,
                    log_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (chain_code, tx_hash, log_index)
                );

                CREATE INDEX IF NOT EXISTS idx_deposits_account
                    ON deposits(account_id, status, updated_at);

                CREATE INDEX IF NOT EXISTS idx_deposits_chain_status
                    ON deposits(chain_code, status, updated_at);

                CREATE TABLE IF NOT EXISTS ledger_entries (
                    entry_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    chain_code TEXT NOT NULL,
                    token_symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    amount_base_units TEXT NOT NULL,
                    reference_type TEXT NOT NULL,
                    reference_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_reference
                    ON ledger_entries(reference_type, reference_id);

                CREATE INDEX IF NOT EXISTS idx_ledger_account_token
                    ON ledger_entries(account_id, chain_code, token_symbol, created_at);

                CREATE TABLE IF NOT EXISTS scanner_checkpoints (
                    scanner_id TEXT PRIMARY KEY,
                    chain_code TEXT NOT NULL,
                    cursor_block INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_scanner_checkpoints_chain
                    ON scanner_checkpoints(chain_code, updated_at);
                """
            )
