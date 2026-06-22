from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from .hashing import hash_json
from .models import (
    Account,
    AccountBalance,
    AccountCreateRequest,
    AssignmentReport,
    AssignmentReportRequest,
    ChainCreateRequest,
    ChainDefinition,
    ChainFamily,
    ConfirmationProcessRequest,
    Deposit,
    DepositStatus,
    ExecutionStatus,
    EvmNativeTransferPollRequest,
    EvmTokenTransferImportRequest,
    EvmTokenTransferPollRequest,
    LedgerDirection,
    LedgerEntry,
    PICO_CURRENCY,
    PayFromBalanceRequest,
    PicoinHistoryImportRequest,
    PicoinNodePollRequest,
    Booking,
    BookingCreateRequest,
    BookingQuote,
    BookingQuoteRequest,
    BookingStatus,
    HardwareType,
    Listing,
    ListingCreateRequest,
    ListingStatus,
    MarketplaceSummary,
    MiningPool,
    MiningPoolCreateRequest,
    PaymentOrder,
    PaymentStatus,
    PaymentSubmitRequest,
    PoolCard,
    PoolStatus,
    ProviderSettlement,
    ScannerDepositCreateRequest,
    SettlementStatus,
    TokenCreateRequest,
    TokenDefinition,
    Wallet,
    WalletCreateRequest,
    WalletStatus,
    Worker,
    WorkerAssignment,
    WorkerHeartbeatRequest,
    WorkerRegisterRequest,
    WorkerStatus,
    utc_now,
)
from .storage import MarketplaceStorage


DEFAULT_STATE_DIR = Path(os.getenv("PICOIN_MARKETPLACE_STATE_DIR", ".picoin-marketplace-state"))
DEFAULT_ESCROW_ADDRESS = os.getenv("PICOIN_MARKETPLACE_ESCROW_ADDRESS", "PI_MARKETPLACE_ESCROW")
DEFAULT_PICOIN_NODE_URL = os.getenv("PICOIN_MARKETPLACE_PICOIN_NODE_URL", "http://127.0.0.1:8000")
DEFAULT_EVM_ESCROW_ADDRESS = os.getenv(
    "PICOIN_MARKETPLACE_EVM_ESCROW_ADDRESS",
    "0x0000000000000000000000000000000000000000",
)
DEFAULT_CONFIRMATIONS_REQUIRED = int(os.getenv("PICOIN_MARKETPLACE_CONFIRMATIONS_REQUIRED", "1"))
EVM_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
EVM_NATIVE_LOG_INDEX = 2_147_483_647
DEFAULT_POOL_SPECS = [
    {
        "hardware_type": HardwareType.CPU,
        "paired_coin": "MONERO",
        "name": "CPU PICO/MONERO pool",
        "metadata": {"algorithm": "randomx", "primary_use": "cpu-mining"},
    },
    {
        "hardware_type": HardwareType.GPU,
        "paired_coin": "RAVENCOIN",
        "name": "GPU PICO/RAVENCOIN pool",
        "metadata": {"algorithm": "kawpow", "primary_use": "gpu-mining"},
    },
    {
        "hardware_type": HardwareType.ASIC,
        "paired_coin": "DOGE",
        "name": "ASIC PICO/DOGE pool",
        "metadata": {"algorithm": "scrypt", "primary_use": "asic-mining"},
    },
    {
        "hardware_type": HardwareType.ASIC,
        "paired_coin": "LITECOIN",
        "name": "ASIC PICO/LITECOIN pool",
        "metadata": {"algorithm": "scrypt", "primary_use": "asic-mining"},
    },
]


class Marketplace:
    def __init__(self, state_dir: str | Path | None = None):
        self.state_dir = Path(state_dir or DEFAULT_STATE_DIR)
        self.storage = MarketplaceStorage(self.state_dir)
        self.ensure_default_payment_registry()

    def ensure_default_payment_registry(self) -> None:
        defaults = [
            ChainCreateRequest(
                chain_code="picoin",
                display_name="Picoin Mainnet",
                family=ChainFamily.PICOIN,
                chain_id=314159,
                confirmations_required=marketplace_confirmations_required(),
                deposit_address=marketplace_escrow_address(),
                rpc_endpoints=[],
            ),
            ChainCreateRequest(
                chain_code="ethereum",
                display_name="Ethereum Mainnet",
                family=ChainFamily.EVM,
                chain_id=1,
                confirmations_required=int(os.getenv("PICOIN_MARKETPLACE_ETH_CONFIRMATIONS", "12")),
                deposit_address=evm_escrow_address(),
                rpc_endpoints=split_env_list("PICOIN_MARKETPLACE_ETH_RPC_URLS"),
            ),
        ]
        for request in defaults:
            if not self.chain_exists(request.chain_code):
                self.create_chain(request)

        token_defaults = [
            TokenCreateRequest(
                chain_code="picoin",
                token_symbol=PICO_CURRENCY,
                display_name="Picoin",
                decimals=6,
                token_type="native",
                pico_rate=1.0,
            ),
            TokenCreateRequest(
                chain_code="ethereum",
                token_symbol="ETH",
                display_name="Ether",
                decimals=18,
                token_type="native",
                pico_rate=float(os.getenv("PICOIN_MARKETPLACE_ETH_PICO_RATE", "1000")),
            ),
        ]
        for request in token_defaults:
            if not self.token_exists(request.chain_code, request.token_symbol):
                self.create_token(request)

    def chain_exists(self, chain_code: str) -> bool:
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM chains WHERE chain_code = ?",
                (normalize_chain_code(chain_code),),
            ).fetchone()
        return row is not None

    def token_exists(self, chain_code: str, token_symbol: str) -> bool:
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM tokens WHERE chain_code = ? AND token_symbol = ?",
                (normalize_chain_code(chain_code), normalize_coin(token_symbol)),
            ).fetchone()
        return row is not None

    def create_chain(self, request: ChainCreateRequest) -> ChainDefinition:
        now = utc_now()
        chain = ChainDefinition(
            chain_code=normalize_chain_code(request.chain_code),
            display_name=request.display_name.strip(),
            family=request.family,
            chain_id=request.chain_id,
            confirmations_required=request.confirmations_required,
            deposit_address=normalize_optional_address(request.family, request.deposit_address),
            rpc_endpoints=request.rpc_endpoints,
            enabled=request.enabled,
            metadata=request.metadata,
            created_at=now,
            updated_at=now,
        )
        self.put_chain(chain)
        return chain

    def put_chain(self, chain: ChainDefinition) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO chains (
                    chain_code,
                    family,
                    enabled,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chain_code) DO UPDATE SET
                    family = excluded.family,
                    enabled = excluded.enabled,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    chain.chain_code,
                    chain.family.value,
                    1 if chain.enabled else 0,
                    chain.model_dump_json(),
                    chain.updated_at.isoformat(),
                ),
            )

    def get_chain(self, chain_code: str) -> ChainDefinition:
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM chains WHERE chain_code = ?",
                (normalize_chain_code(chain_code),),
            ).fetchone()
        if row is None:
            raise KeyError(f"chain not found: {chain_code}")
        return ChainDefinition.model_validate(json.loads(row["payload"]))

    def list_chains(self, *, enabled_only: bool = True) -> list[ChainDefinition]:
        query = "SELECT payload FROM chains"
        params: list[object] = []
        if enabled_only:
            query += " WHERE enabled = ?"
            params.append(1)
        query += " ORDER BY chain_code ASC"
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [ChainDefinition.model_validate(json.loads(row["payload"])) for row in rows]

    def create_token(self, request: TokenCreateRequest) -> TokenDefinition:
        chain = self.get_chain(request.chain_code)
        now = utc_now()
        token = TokenDefinition(
            chain_code=chain.chain_code,
            token_symbol=normalize_coin(request.token_symbol),
            display_name=request.display_name.strip(),
            decimals=request.decimals,
            token_type=request.token_type.strip().lower(),
            contract_address=normalize_optional_address(chain.family, request.contract_address),
            pico_rate=request.pico_rate,
            enabled=request.enabled,
            metadata=request.metadata,
            created_at=now,
            updated_at=now,
        )
        self.put_token(token)
        return token

    def put_token(self, token: TokenDefinition) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO tokens (
                    chain_code,
                    token_symbol,
                    enabled,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chain_code, token_symbol) DO UPDATE SET
                    enabled = excluded.enabled,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    token.chain_code,
                    token.token_symbol,
                    1 if token.enabled else 0,
                    token.model_dump_json(),
                    token.updated_at.isoformat(),
                ),
            )

    def get_token(self, chain_code: str, token_symbol: str) -> TokenDefinition:
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM tokens WHERE chain_code = ? AND token_symbol = ?",
                (normalize_chain_code(chain_code), normalize_coin(token_symbol)),
            ).fetchone()
        if row is None:
            raise KeyError(f"token not found: {chain_code}:{token_symbol}")
        return TokenDefinition.model_validate(json.loads(row["payload"]))

    def list_tokens(self, *, chain_code: str | None = None, enabled_only: bool = True) -> list[TokenDefinition]:
        query = "SELECT payload FROM tokens"
        clauses: list[str] = []
        params: list[object] = []
        if chain_code:
            clauses.append("chain_code = ?")
            params.append(normalize_chain_code(chain_code))
        if enabled_only:
            clauses.append("enabled = ?")
            params.append(1)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY chain_code ASC, token_symbol ASC"
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [TokenDefinition.model_validate(json.loads(row["payload"])) for row in rows]

    def create_account(self, request: AccountCreateRequest) -> Account:
        now = utc_now()
        email = normalize_email(request.email)
        account = Account(
            account_id="acct_" + hash_json({"email": email})[:18],
            email=email,
            display_name=(request.display_name or "").strip() or None,
            created_at=now,
            updated_at=now,
        )
        with self.storage.connect() as connection:
            existing = connection.execute("SELECT payload FROM accounts WHERE email = ?", (email,)).fetchone()
            if existing is not None:
                raise ValueError("account email already exists")
            connection.execute(
                """
                INSERT INTO accounts (
                    account_id,
                    email,
                    status,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    account.account_id,
                    account.email,
                    account.status.value,
                    account.model_dump_json(),
                    account.updated_at.isoformat(),
                ),
            )
        return account

    def get_account(self, account_id: str) -> Account:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
        if row is None:
            raise KeyError(f"account not found: {account_id}")
        return Account.model_validate(json.loads(row["payload"]))

    def list_accounts(self, *, limit: int = 100) -> list[Account]:
        safe_limit = max(1, min(int(limit), 1000))
        with self.storage.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM accounts ORDER BY updated_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [Account.model_validate(json.loads(row["payload"])) for row in rows]

    def create_wallet(self, account_id: str, request: WalletCreateRequest) -> Wallet:
        account = self.get_account(account_id)
        chain = self.get_chain(request.chain_code)
        if not chain.enabled:
            raise ValueError("chain is disabled")
        address = normalize_address(chain.family, request.address)
        now = utc_now()
        wallet = Wallet(
            wallet_id="wallet_" + hash_json(
                {
                    "account_id": account.account_id,
                    "chain_code": chain.chain_code,
                    "address": address,
                }
            )[:18],
            account_id=account.account_id,
            chain_code=chain.chain_code,
            address=address,
            label=(request.label or "").strip() or None,
            created_at=now,
            updated_at=now,
        )
        with self.storage.connect() as connection:
            existing = connection.execute(
                "SELECT payload FROM account_wallets WHERE chain_code = ? AND address = ?",
                (wallet.chain_code, wallet.address),
            ).fetchone()
            if existing is not None:
                existing_wallet = Wallet.model_validate(json.loads(existing["payload"]))
                if existing_wallet.account_id != wallet.account_id:
                    raise ValueError("wallet address already belongs to another account")
                return existing_wallet
            connection.execute(
                """
                INSERT INTO account_wallets (
                    wallet_id,
                    account_id,
                    chain_code,
                    address,
                    status,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    wallet.wallet_id,
                    wallet.account_id,
                    wallet.chain_code,
                    wallet.address,
                    wallet.status.value,
                    wallet.model_dump_json(),
                    wallet.updated_at.isoformat(),
                ),
            )
        return wallet

    def get_wallet(self, wallet_id: str) -> Wallet:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM account_wallets WHERE wallet_id = ?", (wallet_id,)).fetchone()
        if row is None:
            raise KeyError(f"wallet not found: {wallet_id}")
        return Wallet.model_validate(json.loads(row["payload"]))

    def list_wallets(self, *, account_id: str | None = None, limit: int = 100) -> list[Wallet]:
        safe_limit = max(1, min(int(limit), 1000))
        params: list[object] = []
        query = "SELECT payload FROM account_wallets"
        if account_id:
            query += " WHERE account_id = ?"
            params.append(account_id)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [Wallet.model_validate(json.loads(row["payload"])) for row in rows]

    def verify_wallet(self, wallet_id: str) -> Wallet:
        wallet = self.get_wallet(wallet_id)
        now = utc_now()
        wallet.status = WalletStatus.VERIFIED
        wallet.verified_at = wallet.verified_at or now
        wallet.updated_at = now
        self.put_wallet(wallet)
        return wallet

    def put_wallet(self, wallet: Wallet) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                UPDATE account_wallets
                SET status = ?, payload = ?, updated_at = ?
                WHERE wallet_id = ?
                """,
                (
                    wallet.status.value,
                    wallet.model_dump_json(),
                    wallet.updated_at.isoformat(),
                    wallet.wallet_id,
                ),
            )

    def scanner_config(self, chain_code: str) -> dict:
        chain = self.get_chain(chain_code)
        tokens = self.list_tokens(chain_code=chain.chain_code, enabled_only=True)
        return {
            "chain": chain.model_dump(mode="json"),
            "deposit_address": chain.deposit_address,
            "tokens": [token.model_dump(mode="json") for token in tokens],
        }

    def accept_deposit(self, request: ScannerDepositCreateRequest) -> Deposit:
        chain = self.get_chain(request.chain_code)
        if not chain.enabled:
            raise ValueError("chain is disabled")
        token = self.get_token(chain.chain_code, request.token_symbol)
        if not token.enabled:
            raise ValueError("token is disabled")
        from_address = normalize_address(chain.family, request.from_address)
        to_address = normalize_address(chain.family, request.to_address)
        if chain.deposit_address and to_address != chain.deposit_address:
            raise ValueError("deposit destination does not match marketplace deposit address")
        amount_base_units = assert_positive_base_units(request.amount_base_units)
        tx_hash = normalize_tx_hash(chain.family, request.tx_hash)
        block_hash = normalize_tx_hash(chain.family, request.block_hash) if request.block_hash else None
        wallet = self.find_verified_wallet(chain.chain_code, from_address)
        if wallet is None:
            raise KeyError("verified origin wallet not found")
        now = utc_now()
        deposit = Deposit(
            deposit_id="deposit_" + hash_json(
                {
                    "chain_code": chain.chain_code,
                    "tx_hash": tx_hash,
                    "log_index": request.log_index,
                }
            )[:18],
            account_id=wallet.account_id,
            wallet_id=wallet.wallet_id,
            chain_code=chain.chain_code,
            token_symbol=token.token_symbol,
            from_address=from_address,
            to_address=to_address,
            amount_base_units=amount_base_units,
            tx_hash=tx_hash,
            block_number=request.block_number,
            block_hash=block_hash,
            log_index=request.log_index,
            status=request.status,
            created_at=now,
            updated_at=now,
        )
        with self.storage.connect() as connection:
            existing = connection.execute(
                """
                SELECT payload FROM deposits
                WHERE chain_code = ? AND tx_hash = ? AND log_index = ?
                """,
                (deposit.chain_code, deposit.tx_hash, deposit.log_index),
            ).fetchone()
            if existing is not None:
                existing_deposit = Deposit.model_validate(json.loads(existing["payload"]))
                if not deposits_match(existing_deposit, deposit):
                    raise ValueError("deposit event already exists with different payload")
                return existing_deposit
            connection.execute(
                """
                INSERT INTO deposits (
                    deposit_id,
                    account_id,
                    wallet_id,
                    chain_code,
                    token_symbol,
                    tx_hash,
                    log_index,
                    status,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deposit.deposit_id,
                    deposit.account_id,
                    deposit.wallet_id,
                    deposit.chain_code,
                    deposit.token_symbol,
                    deposit.tx_hash,
                    deposit.log_index,
                    deposit.status.value,
                    deposit.model_dump_json(),
                    deposit.updated_at.isoformat(),
                ),
            )
        return deposit

    def process_confirmations(self, chain_code: str, request: ConfirmationProcessRequest) -> dict:
        chain = self.get_chain(chain_code)
        latest = request.latest_block_number
        processed = 0
        credited = 0
        with self.storage.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM deposits
                WHERE chain_code = ?
                  AND status IN (?, ?, ?)
                ORDER BY json_extract(payload, '$.block_number') ASC, deposit_id ASC
                """,
                (
                    chain.chain_code,
                    DepositStatus.DETECTED.value,
                    DepositStatus.CONFIRMING.value,
                    DepositStatus.CONFIRMED.value,
                ),
            ).fetchall()
        for row in rows:
            deposit = Deposit.model_validate(json.loads(row["payload"]))
            if latest < deposit.block_number:
                continue
            processed += 1
            confirmations = latest - deposit.block_number + 1
            deposit.confirmations = confirmations
            deposit.updated_at = utc_now()
            if confirmations < chain.confirmations_required:
                if deposit.status == DepositStatus.DETECTED:
                    deposit.status = DepositStatus.CONFIRMING
                self.put_deposit(deposit)
                continue
            if deposit.status != DepositStatus.CREDITED:
                deposit.status = DepositStatus.CONFIRMED
                deposit.confirmed_at = deposit.confirmed_at or deposit.updated_at
                self.put_deposit(deposit)
                self.credit_deposit(deposit.deposit_id)
                credited += 1
        return {
            "chain_code": chain.chain_code,
            "latest_block_number": latest,
            "processed": processed,
            "credited": credited,
        }

    def import_picoin_history(self, request: PicoinHistoryImportRequest) -> dict:
        chain = self.get_chain("picoin")
        token = self.get_token(chain.chain_code, PICO_CURRENCY)
        imported = 0
        skipped = 0
        errors: list[dict[str, object]] = []
        deposits: list[Deposit] = []
        for index, row in enumerate(request.rows):
            try:
                deposit_request = picoin_history_row_to_deposit_request(row, chain.deposit_address)
                if deposit_request is None:
                    skipped += 1
                    continue
                if deposit_request.chain_code != chain.chain_code or deposit_request.token_symbol != token.token_symbol:
                    skipped += 1
                    continue
                deposit = self.accept_deposit(deposit_request)
                deposits.append(deposit)
                imported += 1
            except (KeyError, ValueError) as exc:
                skipped += 1
                errors.append({"row": index, "error": str(exc)})
        confirmation_result = None
        if request.latest_block_number:
            confirmation_result = self.process_confirmations(
                chain.chain_code,
                ConfirmationProcessRequest(latest_block_number=request.latest_block_number),
            )
        return {
            "chain_code": chain.chain_code,
            "rows_seen": len(request.rows),
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "confirmation_result": confirmation_result,
            "deposits": [deposit.model_dump(mode="json") for deposit in deposits],
        }

    def poll_picoin_node(self, request: PicoinNodePollRequest) -> dict:
        chain = self.get_chain("picoin")
        address = normalize_address(chain.family, request.address or chain.deposit_address or marketplace_escrow_address())
        node_url = (request.node_url or os.getenv("PICOIN_MARKETPLACE_PICOIN_NODE_URL", DEFAULT_PICOIN_NODE_URL)).rstrip("/")
        params = urllib.parse.urlencode(
            {
                "address": address,
                "limit": request.limit,
                "confirmed_only": str(request.confirmed_only).lower(),
                "backfill": "true",
            }
        )
        url = f"{node_url}/transactions/history?{params}"
        rows = fetch_json_url(url)
        if not isinstance(rows, list):
            raise ValueError("Picoin node history endpoint did not return a list")
        result = self.import_picoin_history(
            PicoinHistoryImportRequest(
                rows=rows,
                latest_block_number=request.latest_block_number or latest_block_height_from_history(rows),
            )
        )
        result["node_url"] = node_url
        result["history_address"] = address
        return result

    def import_evm_token_transfers(self, request: EvmTokenTransferImportRequest) -> dict:
        chain = self.get_chain(request.chain_code)
        if chain.family != ChainFamily.EVM:
            raise ValueError("chain is not EVM")
        token = self.get_token(chain.chain_code, request.token_symbol)
        if not token.contract_address:
            raise ValueError("token has no ERC20 contract address")
        imported = 0
        skipped = 0
        errors: list[dict[str, object]] = []
        deposits: list[Deposit] = []
        for index, log in enumerate(request.logs):
            try:
                deposit_request = evm_transfer_log_to_deposit_request(log, chain, token)
                if deposit_request is None:
                    skipped += 1
                    continue
                deposit = self.accept_deposit(deposit_request)
                deposits.append(deposit)
                imported += 1
            except (KeyError, ValueError) as exc:
                skipped += 1
                errors.append({"log": index, "error": str(exc)})
        confirmation_result = None
        if request.latest_block_number:
            confirmation_result = self.process_confirmations(
                chain.chain_code,
                ConfirmationProcessRequest(latest_block_number=request.latest_block_number),
            )
        return {
            "chain_code": chain.chain_code,
            "token_symbol": token.token_symbol,
            "logs_seen": len(request.logs),
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "confirmation_result": confirmation_result,
            "deposits": [deposit.model_dump(mode="json") for deposit in deposits],
        }

    def poll_evm_token_transfers(self, request: EvmTokenTransferPollRequest) -> dict:
        chain = self.get_chain(request.chain_code)
        if chain.family != ChainFamily.EVM:
            raise ValueError("chain is not EVM")
        rpc_url = resolve_rpc_url(chain, request.rpc_url)
        latest = request.to_block if request.to_block is not None else evm_block_number(rpc_url)
        tokens = self.evm_tokens_for_poll(chain.chain_code, request.token_symbol)
        results = []
        total_imported = 0
        total_skipped = 0
        for token in tokens:
            scanner_id = evm_token_scanner_id(chain.chain_code, token.token_symbol)
            from_block = resolve_scan_from_block(
                explicit_from=request.from_block,
                checkpoint=self.get_scanner_checkpoint(scanner_id),
                latest=latest,
                batch_size=request.batch_size,
            )
            to_block = min(latest, from_block + request.batch_size - 1)
            logs = evm_get_transfer_logs(rpc_url, token.contract_address or "", chain.deposit_address or "", from_block, to_block)
            result = self.import_evm_token_transfers(
                EvmTokenTransferImportRequest(
                    chain_code=chain.chain_code,
                    token_symbol=token.token_symbol,
                    logs=logs,
                    latest_block_number=request.latest_block_number or latest,
                )
            )
            self.set_scanner_checkpoint(
                scanner_id,
                chain.chain_code,
                to_block,
                {"token_symbol": token.token_symbol, "mode": "erc20_transfer"},
            )
            result["from_block"] = from_block
            result["to_block"] = to_block
            results.append(result)
            total_imported += int(result["imported"])
            total_skipped += int(result["skipped"])
        return {
            "chain_code": chain.chain_code,
            "rpc_url": rpc_url,
            "token_count": len(tokens),
            "imported": total_imported,
            "skipped": total_skipped,
            "results": results,
        }

    def poll_evm_native_transfers(self, request: EvmNativeTransferPollRequest) -> dict:
        chain = self.get_chain(request.chain_code)
        if chain.family != ChainFamily.EVM:
            raise ValueError("chain is not EVM")
        token = self.get_token(chain.chain_code, request.token_symbol)
        if token.token_type != "native":
            raise ValueError("token is not native")
        rpc_url = resolve_rpc_url(chain, request.rpc_url)
        latest = request.to_block if request.to_block is not None else evm_block_number(rpc_url)
        scanner_id = evm_native_scanner_id(chain.chain_code, token.token_symbol)
        from_block = resolve_scan_from_block(
            explicit_from=request.from_block,
            checkpoint=self.get_scanner_checkpoint(scanner_id),
            latest=latest,
            batch_size=request.batch_size,
        )
        to_block = min(latest, from_block + request.batch_size - 1)
        imported = 0
        skipped = 0
        errors: list[dict[str, object]] = []
        deposits: list[Deposit] = []
        for height in range(from_block, to_block + 1):
            block = evm_get_block(rpc_url, height)
            for tx_index, tx in enumerate(block.get("transactions") or []):
                try:
                    deposit_request = evm_native_tx_to_deposit_request(tx, block, chain, token, tx_index)
                    if deposit_request is None:
                        skipped += 1
                        continue
                    deposit = self.accept_deposit(deposit_request)
                    deposits.append(deposit)
                    imported += 1
                except (KeyError, ValueError) as exc:
                    skipped += 1
                    errors.append({"block": height, "tx": tx_index, "error": str(exc)})
        confirmation_result = self.process_confirmations(
            chain.chain_code,
            ConfirmationProcessRequest(latest_block_number=request.latest_block_number or latest),
        )
        self.set_scanner_checkpoint(
            scanner_id,
            chain.chain_code,
            to_block,
            {"token_symbol": token.token_symbol, "mode": "native_transfer"},
        )
        return {
            "chain_code": chain.chain_code,
            "token_symbol": token.token_symbol,
            "rpc_url": rpc_url,
            "from_block": from_block,
            "to_block": to_block,
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "confirmation_result": confirmation_result,
            "deposits": [deposit.model_dump(mode="json") for deposit in deposits],
        }

    def evm_tokens_for_poll(self, chain_code: str, token_symbol: str | None) -> list[TokenDefinition]:
        if token_symbol:
            token = self.get_token(chain_code, token_symbol)
            if not token.contract_address:
                raise ValueError("token has no ERC20 contract address")
            return [token]
        return [
            token
            for token in self.list_tokens(chain_code=chain_code, enabled_only=True)
            if token.contract_address
        ]

    def get_scanner_checkpoint(self, scanner_id: str) -> int | None:
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT cursor_block FROM scanner_checkpoints WHERE scanner_id = ?",
                (scanner_id,),
            ).fetchone()
        return int(row["cursor_block"]) if row else None

    def set_scanner_checkpoint(self, scanner_id: str, chain_code: str, cursor_block: int, payload: dict[str, object]) -> None:
        now = utc_now()
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO scanner_checkpoints (
                    scanner_id,
                    chain_code,
                    cursor_block,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scanner_id) DO UPDATE SET
                    chain_code = excluded.chain_code,
                    cursor_block = excluded.cursor_block,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    scanner_id,
                    normalize_chain_code(chain_code),
                    int(cursor_block),
                    json.dumps(payload, sort_keys=True),
                    now.isoformat(),
                ),
            )

    def put_deposit(self, deposit: Deposit) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                UPDATE deposits
                SET status = ?, payload = ?, updated_at = ?
                WHERE deposit_id = ?
                """,
                (
                    deposit.status.value,
                    deposit.model_dump_json(),
                    deposit.updated_at.isoformat(),
                    deposit.deposit_id,
                ),
            )

    def credit_deposit(self, deposit_id: str) -> LedgerEntry:
        deposit = self.get_deposit(deposit_id)
        if deposit.status not in {DepositStatus.CONFIRMED, DepositStatus.CREDITED}:
            raise ValueError("deposit is not confirmed")
        existing = self.find_ledger_reference("deposit", deposit.deposit_id)
        if existing is not None:
            if deposit.status != DepositStatus.CREDITED:
                deposit.status = DepositStatus.CREDITED
                deposit.credited_at = deposit.credited_at or utc_now()
                deposit.updated_at = deposit.credited_at
                self.put_deposit(deposit)
            return existing
        entry = LedgerEntry(
            entry_id="ledger_" + hash_json({"reference_type": "deposit", "reference_id": deposit.deposit_id})[:18],
            account_id=deposit.account_id,
            chain_code=deposit.chain_code,
            token_symbol=deposit.token_symbol,
            direction=LedgerDirection.CREDIT,
            amount_base_units=deposit.amount_base_units,
            entry_type="deposit",
            reference_type="deposit",
            reference_id=deposit.deposit_id,
        )
        self.put_ledger_entry(entry)
        deposit.status = DepositStatus.CREDITED
        deposit.credited_at = utc_now()
        deposit.updated_at = deposit.credited_at
        self.put_deposit(deposit)
        return entry

    def get_deposit(self, deposit_id: str) -> Deposit:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM deposits WHERE deposit_id = ?", (deposit_id,)).fetchone()
        if row is None:
            raise KeyError(f"deposit not found: {deposit_id}")
        return Deposit.model_validate(json.loads(row["payload"]))

    def list_deposits(self, *, account_id: str | None = None, limit: int = 100) -> list[Deposit]:
        safe_limit = max(1, min(int(limit), 1000))
        params: list[object] = []
        query = "SELECT payload FROM deposits"
        if account_id:
            query += " WHERE account_id = ?"
            params.append(account_id)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [Deposit.model_validate(json.loads(row["payload"])) for row in rows]

    def find_verified_wallet(self, chain_code: str, address: str) -> Wallet | None:
        with self.storage.connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM account_wallets
                WHERE chain_code = ? AND address = ? AND status = ?
                """,
                (normalize_chain_code(chain_code), address, WalletStatus.VERIFIED.value),
            ).fetchone()
        if row is None:
            return None
        return Wallet.model_validate(json.loads(row["payload"]))

    def put_ledger_entry(self, entry: LedgerEntry) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO ledger_entries (
                    entry_id,
                    account_id,
                    chain_code,
                    token_symbol,
                    direction,
                    amount_base_units,
                    reference_type,
                    reference_id,
                    payload,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reference_type, reference_id) DO NOTHING
                """,
                (
                    entry.entry_id,
                    entry.account_id,
                    entry.chain_code,
                    entry.token_symbol,
                    entry.direction.value,
                    entry.amount_base_units,
                    entry.reference_type,
                    entry.reference_id,
                    entry.model_dump_json(),
                    entry.created_at.isoformat(),
                ),
            )

    def find_ledger_reference(self, reference_type: str, reference_id: str) -> LedgerEntry | None:
        with self.storage.connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM ledger_entries
                WHERE reference_type = ? AND reference_id = ?
                """,
                (reference_type, reference_id),
            ).fetchone()
        if row is None:
            return None
        return LedgerEntry.model_validate(json.loads(row["payload"]))

    def list_ledger(self, *, account_id: str | None = None, limit: int = 100) -> list[LedgerEntry]:
        safe_limit = max(1, min(int(limit), 1000))
        params: list[object] = []
        query = "SELECT payload FROM ledger_entries"
        if account_id:
            query += " WHERE account_id = ?"
            params.append(account_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [LedgerEntry.model_validate(json.loads(row["payload"])) for row in rows]

    def account_balances(self, account_id: str) -> list[AccountBalance]:
        self.get_account(account_id)
        totals: dict[tuple[str, str], int] = {}
        for entry in self.list_ledger(account_id=account_id, limit=1000):
            key = (entry.chain_code, entry.token_symbol)
            amount = int(entry.amount_base_units)
            totals[key] = totals.get(key, 0) + (amount if entry.direction == LedgerDirection.CREDIT else -amount)
        balances: list[AccountBalance] = []
        for token in self.list_tokens(enabled_only=True):
            amount = totals.get((token.chain_code, token.token_symbol), 0)
            balances.append(
                AccountBalance(
                    account_id=account_id,
                    chain_code=token.chain_code,
                    token_symbol=token.token_symbol,
                    decimals=token.decimals,
                    available_base_units=str(amount),
                    available=format_base_units(amount, token.decimals),
                )
            )
        return balances

    def pay_payment_from_balance(
        self,
        payment_id: str,
        request: PayFromBalanceRequest,
    ) -> tuple[Booking, PaymentOrder, LedgerEntry]:
        payment = self.get_payment(payment_id)
        booking = self.get_booking(payment.booking_id)
        if payment.status == PaymentStatus.CONFIRMED:
            existing = self.find_ledger_reference("payment", payment.payment_id)
            if existing is None:
                raise ValueError("payment is confirmed but no ledger debit was found")
            return booking, payment, existing
        token = self.get_token(request.chain_code, request.token_symbol)
        if token.pico_rate is None:
            raise ValueError("token has no PICO rate configured for marketplace payments")
        required_units = amount_pi_to_base_units(payment.amount_pi, token)
        balance = self.available_balance(request.account_id, token.chain_code, token.token_symbol)
        if balance < required_units:
            raise ValueError(
                f"insufficient {token.token_symbol} balance: required {required_units}, available {balance}"
            )
        now = utc_now()
        entry = LedgerEntry(
            entry_id="ledger_" + hash_json({"reference_type": "payment", "reference_id": payment.payment_id})[:18],
            account_id=request.account_id,
            chain_code=token.chain_code,
            token_symbol=token.token_symbol,
            direction=LedgerDirection.DEBIT,
            amount_base_units=str(required_units),
            entry_type="marketplace_payment",
            reference_type="payment",
            reference_id=payment.payment_id,
            created_at=now,
        )
        self.put_ledger_entry(entry)
        payment.account_id = request.account_id
        payment.currency = token.token_symbol
        payment.payment_chain_code = token.chain_code
        payment.payment_token_symbol = token.token_symbol
        payment.amount_base_units = str(required_units)
        payment.status = PaymentStatus.CONFIRMED
        payment.confirmations = 0
        payment.updated_at = now
        if booking.status == BookingStatus.AWAITING_PAYMENT:
            booking.status = BookingStatus.ACTIVE
            booking.starts_at = now
            booking.expires_at = now + timedelta(minutes=booking.duration_minutes)
            booking.updated_at = now
            self.put_booking(booking)
        self.put_payment(payment)
        return booking, payment, entry

    def available_balance(self, account_id: str, chain_code: str, token_symbol: str) -> int:
        self.get_account(account_id)
        total = 0
        with self.storage.connect() as connection:
            rows = connection.execute(
                """
                SELECT direction, amount_base_units
                FROM ledger_entries
                WHERE account_id = ? AND chain_code = ? AND token_symbol = ?
                """,
                (account_id, normalize_chain_code(chain_code), normalize_coin(token_symbol)),
            ).fetchall()
        for row in rows:
            amount = int(row["amount_base_units"])
            total += amount if row["direction"] == LedgerDirection.CREDIT.value else -amount
        return total

    def ensure_default_pools(self) -> list[MiningPool]:
        pools: list[MiningPool] = []
        for spec in DEFAULT_POOL_SPECS:
            request = MiningPoolCreateRequest(**spec)
            pool_id = pool_id_for(request.hardware_type, request.paired_coin)
            try:
                pools.append(self.get_pool(pool_id))
            except KeyError:
                pools.append(self.create_pool(request))
        return pools

    def create_pool(self, request: MiningPoolCreateRequest) -> MiningPool:
        validate_capacity_split(request.picoin_capacity_percent, request.paired_capacity_percent)
        now = utc_now()
        paired_coin = normalize_coin(request.paired_coin)
        pair_symbol = f"{PICO_CURRENCY}/{paired_coin}"
        pool = MiningPool(
            pool_id=pool_id_for(request.hardware_type, paired_coin),
            hardware_type=request.hardware_type,
            paired_coin=paired_coin,
            pair_symbol=pair_symbol,
            name=(request.name or f"{request.hardware_type.value.upper()} {pair_symbol} pool").strip(),
            picoin_capacity_percent=round(float(request.picoin_capacity_percent), 4),
            paired_capacity_percent=round(float(request.paired_capacity_percent), 4),
            metadata=request.metadata,
            created_at=now,
            updated_at=now,
        )
        self.put_pool(pool)
        return pool

    def get_pool(self, pool_id: str) -> MiningPool:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM mining_pools WHERE pool_id = ?", (pool_id,)).fetchone()
        if row is None:
            raise KeyError(f"pool not found: {pool_id}")
        return MiningPool.model_validate(json.loads(row["payload"]))

    def list_pools(
        self,
        *,
        hardware_type: HardwareType | None = None,
        paired_coin: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[MiningPool]:
        safe_limit = max(1, min(int(limit), 1000))
        params: list[object] = []
        query = "SELECT payload FROM mining_pools"
        clauses: list[str] = []
        if hardware_type:
            clauses.append("hardware_type = ?")
            params.append(hardware_type.value)
        if paired_coin:
            clauses.append("paired_coin = ?")
            params.append(normalize_coin(paired_coin))
        if active_only:
            clauses.append("status = ?")
            params.append(PoolStatus.ACTIVE.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [MiningPool.model_validate(json.loads(row["payload"])) for row in rows]

    def pool_cards(
        self,
        *,
        hardware_type: HardwareType | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[PoolCard]:
        pools = self.list_pools(hardware_type=hardware_type, active_only=active_only, limit=limit)
        cards: list[PoolCard] = []
        for pool in pools:
            listings = self.list_listings(pool_id=pool.pool_id, active_only=False, limit=1000)
            active_listings = [listing for listing in listings if listing.status == ListingStatus.ACTIVE]
            total_units = sum(listing.units_total for listing in listings)
            available_units = sum(listing.units_available for listing in active_listings)
            booked_units = max(0, total_units - available_units)
            active_prices = [
                listing.price_pi_per_hour
                for listing in active_listings
                if listing.units_available > 0
            ]
            min_price = min(active_prices) if active_prices else None
            cards.append(
                PoolCard(
                    pool_id=pool.pool_id,
                    hardware_type=pool.hardware_type,
                    paired_coin=pool.paired_coin,
                    pair_symbol=pool.pair_symbol,
                    name=pool.name,
                    algorithm=pool.metadata.get("algorithm"),
                    picoin_capacity_percent=pool.picoin_capacity_percent,
                    paired_capacity_percent=pool.paired_capacity_percent,
                    active_listing_count=len(active_listings),
                    total_units=total_units,
                    available_units=available_units,
                    booked_units=booked_units,
                    min_price_pi_per_hour=min_price,
                    estimated_one_hour_pi=min_price,
                    can_book=available_units > 0 and min_price is not None,
                    status="available" if available_units > 0 else "waiting_capacity",
                )
            )
        return sorted(cards, key=lambda card: (not card.can_book, card.hardware_type.value, card.pair_symbol))

    def put_pool(self, pool: MiningPool) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO mining_pools (
                    pool_id,
                    hardware_type,
                    paired_coin,
                    pair_symbol,
                    status,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pool_id) DO UPDATE SET
                    hardware_type = excluded.hardware_type,
                    paired_coin = excluded.paired_coin,
                    pair_symbol = excluded.pair_symbol,
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    pool.pool_id,
                    pool.hardware_type.value,
                    pool.paired_coin,
                    pool.pair_symbol,
                    pool.status.value,
                    pool.model_dump_json(),
                    pool.updated_at.isoformat(),
                ),
            )

    def create_listing(self, request: ListingCreateRequest) -> Listing:
        now = utc_now()
        provider_wallet = request.provider_wallet.strip().upper()
        pool = self.get_pool(request.pool_id)
        if pool.status != PoolStatus.ACTIVE:
            raise ValueError("pool is not active")
        if pool.hardware_type != request.hardware_type:
            raise ValueError("listing hardware_type must match pool hardware_type")
        listing = Listing(
            listing_id="listing_" + hash_json(
                {
                    "pool_id": pool.pool_id,
                    "provider_id": request.provider_id,
                    "provider_wallet": provider_wallet,
                    "hardware_type": request.hardware_type.value,
                    "title": request.title,
                    "created_at": now.isoformat(),
                }
            )[:18],
            pool_id=pool.pool_id,
            pair_symbol=pool.pair_symbol,
            paired_coin=pool.paired_coin,
            picoin_capacity_percent=pool.picoin_capacity_percent,
            paired_capacity_percent=pool.paired_capacity_percent,
            provider_id=request.provider_id.strip(),
            provider_wallet=provider_wallet,
            hardware_type=request.hardware_type,
            title=request.title.strip(),
            units_total=request.units_total,
            units_available=request.units_total,
            price_pi_per_hour=round(float(request.price_pi_per_hour), 8),
            min_booking_minutes=request.min_booking_minutes,
            region=request.region,
            capabilities=normalize_capabilities(request.capabilities),
            cpu_threads=request.cpu_threads,
            memory_gb=request.memory_gb,
            gpu_model=request.gpu_model,
            gpu_count=request.gpu_count,
            gpu_vram_gb=request.gpu_vram_gb,
            asic_algorithm=request.asic_algorithm,
            asic_hashrate_th_s=request.asic_hashrate_th_s,
            metadata=request.metadata,
            created_at=now,
            updated_at=now,
        )
        self.put_listing(listing)
        return listing

    def get_listing(self, listing_id: str) -> Listing:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM listings WHERE listing_id = ?", (listing_id,)).fetchone()
        if row is None:
            raise KeyError(f"listing not found: {listing_id}")
        return Listing.model_validate(json.loads(row["payload"]))

    def list_listings(
        self,
        *,
        pool_id: str | None = None,
        hardware_type: HardwareType | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[Listing]:
        safe_limit = max(1, min(int(limit), 1000))
        params: list[object] = []
        query = "SELECT payload FROM listings"
        clauses: list[str] = []
        if pool_id:
            clauses.append("pool_id = ?")
            params.append(pool_id)
        if hardware_type:
            clauses.append("hardware_type = ?")
            params.append(hardware_type.value)
        if active_only:
            clauses.append("status = ?")
            params.append(ListingStatus.ACTIVE.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [Listing.model_validate(json.loads(row["payload"])) for row in rows]

    def put_listing(self, listing: Listing) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO listings (
                    listing_id,
                    pool_id,
                    provider_id,
                    provider_wallet,
                    hardware_type,
                    status,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(listing_id) DO UPDATE SET
                    pool_id = excluded.pool_id,
                    provider_id = excluded.provider_id,
                    provider_wallet = excluded.provider_wallet,
                    hardware_type = excluded.hardware_type,
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    listing.listing_id,
                    listing.pool_id,
                    listing.provider_id,
                    listing.provider_wallet,
                    listing.hardware_type.value,
                    listing.status.value,
                    listing.model_dump_json(),
                    listing.updated_at.isoformat(),
                ),
            )

    def register_worker(self, request: WorkerRegisterRequest) -> tuple[Worker, Listing]:
        pool = self.get_pool(request.pool_id)
        if pool.status != PoolStatus.ACTIVE:
            raise ValueError("pool is not active")
        if pool.hardware_type != request.hardware_type:
            raise ValueError("worker hardware_type must match pool hardware_type")
        now = utc_now()
        provider_wallet = request.provider_wallet.strip().upper()
        worker_id = normalize_worker_id(
            request.worker_id
            or "worker_"
            + hash_json(
                {
                    "provider_id": request.provider_id.strip(),
                    "provider_wallet": provider_wallet,
                    "pool_id": pool.pool_id,
                    "hardware_type": request.hardware_type.value,
                }
            )[:18]
        )
        listing_id = "listing_" + hash_json({"worker_id": worker_id, "pool_id": pool.pool_id})[:18]
        try:
            existing_listing = self.get_listing(listing_id)
            listing_created_at = existing_listing.created_at
        except KeyError:
            listing_created_at = now
        try:
            existing_worker = self.get_worker(worker_id)
            worker_created_at = existing_worker.created_at
        except KeyError:
            worker_created_at = now
        reserved_units = self.reserved_units_for_listing(listing_id)
        units_available = max(0, request.units_total - reserved_units)
        listing = Listing(
            listing_id=listing_id,
            pool_id=pool.pool_id,
            pair_symbol=pool.pair_symbol,
            paired_coin=pool.paired_coin,
            picoin_capacity_percent=pool.picoin_capacity_percent,
            paired_capacity_percent=pool.paired_capacity_percent,
            provider_id=request.provider_id.strip(),
            provider_wallet=provider_wallet,
            hardware_type=request.hardware_type,
            title=(request.title or f"{request.provider_id.strip()} {pool.pair_symbol} worker").strip(),
            units_total=request.units_total,
            units_available=units_available,
            price_pi_per_hour=round(float(request.price_pi_per_hour), 8),
            min_booking_minutes=request.min_booking_minutes,
            region=request.region,
            capabilities=normalize_capabilities(request.capabilities),
            cpu_threads=request.cpu_threads,
            memory_gb=request.memory_gb,
            gpu_model=request.gpu_model,
            gpu_count=request.gpu_count,
            gpu_vram_gb=request.gpu_vram_gb,
            asic_algorithm=request.asic_algorithm,
            asic_hashrate_th_s=request.asic_hashrate_th_s,
            metadata={
                **request.metadata,
                "worker_id": worker_id,
                "worker_status": WorkerStatus.ONLINE.value,
            },
            created_at=listing_created_at,
            updated_at=now,
        )
        worker = Worker(
            worker_id=worker_id,
            listing_id=listing.listing_id,
            provider_id=listing.provider_id,
            pool_id=pool.pool_id,
            hardware_type=request.hardware_type,
            status=WorkerStatus.ONLINE,
            endpoint_url=request.endpoint_url,
            agent_version=request.agent_version,
            last_seen_at=now,
            metadata=request.metadata,
            created_at=worker_created_at,
            updated_at=now,
        )
        self.put_listing(listing)
        self.put_worker(worker)
        return worker, listing

    def heartbeat_worker(self, worker_id: str, request: WorkerHeartbeatRequest) -> tuple[Worker, Listing]:
        worker = self.get_worker(worker_id)
        listing = self.get_listing(worker.listing_id)
        now = utc_now()
        worker.status = request.status
        worker.last_seen_at = now
        worker.updated_at = now
        worker.metrics = request.metrics
        if request.endpoint_url is not None:
            worker.endpoint_url = request.endpoint_url
        if request.agent_version is not None:
            worker.agent_version = request.agent_version
        if request.metadata:
            worker.metadata = {**worker.metadata, **request.metadata}

        units_total = request.units_total or listing.units_total
        reserved_units = self.reserved_units_for_listing(listing.listing_id)
        listing.units_total = units_total
        if request.status == WorkerStatus.ONLINE:
            reported_available = listing.units_available if request.units_available is None else request.units_available
            listing.units_available = max(0, min(reported_available, units_total - reserved_units))
            listing.status = ListingStatus.ACTIVE
        else:
            listing.units_available = 0
            listing.status = ListingStatus.PAUSED
        listing.metadata = {
            **listing.metadata,
            "worker_id": worker.worker_id,
            "worker_status": worker.status.value,
            "worker_last_seen_at": now.isoformat(),
        }
        listing.updated_at = now
        self.put_listing(listing)
        self.put_worker(worker)
        return worker, listing

    def get_worker(self, worker_id: str) -> Worker:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM workers WHERE worker_id = ?", (worker_id,)).fetchone()
        if row is None:
            raise KeyError(f"worker not found: {worker_id}")
        return Worker.model_validate(json.loads(row["payload"]))

    def list_workers(
        self,
        *,
        provider_id: str | None = None,
        pool_id: str | None = None,
        status: WorkerStatus | None = None,
        limit: int = 100,
    ) -> list[Worker]:
        safe_limit = max(1, min(int(limit), 1000))
        params: list[object] = []
        query = "SELECT payload FROM workers"
        clauses: list[str] = []
        if provider_id:
            clauses.append("provider_id = ?")
            params.append(provider_id.strip())
        if pool_id:
            clauses.append("pool_id = ?")
            params.append(pool_id)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [Worker.model_validate(json.loads(row["payload"])) for row in rows]

    def worker_assignments(
        self,
        worker_id: str,
        *,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[WorkerAssignment]:
        worker = self.get_worker(worker_id)
        listing = self.get_listing(worker.listing_id)
        safe_limit = max(1, min(int(limit), 1000))
        params: list[object] = [listing.listing_id]
        query = "SELECT payload FROM bookings WHERE listing_id = ?"
        if active_only:
            query += " AND status = ?"
            params.append(BookingStatus.ACTIVE.value)
        else:
            query += " AND status IN (?, ?)"
            params.extend([BookingStatus.AWAITING_PAYMENT.value, BookingStatus.ACTIVE.value])
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        assignments: list[WorkerAssignment] = []
        for row in rows:
            booking = Booking.model_validate(json.loads(row["payload"]))
            latest_report = self.latest_assignment_report(booking_id=booking.booking_id)
            assignments.append(
                WorkerAssignment(
                    assignment_id="assign_" + hash_json({"worker_id": worker.worker_id, "booking_id": booking.booking_id})[:18],
                    worker_id=worker.worker_id,
                    booking_id=booking.booking_id,
                    listing_id=booking.listing_id,
                    pool_id=booking.pool_id,
                    provider_id=booking.provider_id,
                    requester_wallet=booking.requester_wallet,
                    hardware_type=booking.hardware_type,
                    pair_symbol=booking.pair_symbol,
                    paired_coin=booking.paired_coin,
                    units=booking.units,
                    duration_minutes=booking.duration_minutes,
                    amount_pi=booking.amount_pi,
                    status=booking.status,
                    picoin_capacity_percent=booking.picoin_capacity_percent,
                    paired_capacity_percent=booking.paired_capacity_percent,
                    picoin_capacity_units=booking.picoin_capacity_units,
                    paired_capacity_units=booking.paired_capacity_units,
                    starts_at=booking.starts_at,
                    expires_at=booking.expires_at,
                    created_at=booking.created_at,
                    updated_at=booking.updated_at,
                    progress_percent=self.booking_progress_percent(booking),
                    latest_report=latest_report.model_dump(mode="json") if latest_report else None,
                    metadata=booking.metadata,
                )
            )
        return assignments

    def booking_progress_percent(self, booking: Booking, *, now: datetime | None = None) -> float:
        if booking.status != BookingStatus.ACTIVE or booking.starts_at is None or booking.expires_at is None:
            return 0.0
        current = now if now is not None else utc_now()
        total_seconds = (booking.expires_at - booking.starts_at).total_seconds()
        if total_seconds <= 0:
            return 100.0
        elapsed_seconds = (current - booking.starts_at).total_seconds()
        return round(max(0.0, min(100.0, elapsed_seconds * 100.0 / total_seconds)), 2)

    def report_assignment(
        self,
        worker_id: str,
        booking_id: str,
        request: AssignmentReportRequest,
    ) -> AssignmentReport:
        worker = self.get_worker(worker_id)
        booking = self.get_booking(booking_id)
        if booking.listing_id != worker.listing_id:
            raise ValueError("booking is not assigned to this worker")
        if booking.status != BookingStatus.ACTIVE:
            raise ValueError("booking is not active")
        now = utc_now()
        report = AssignmentReport(
            report_id="report_"
            + hash_json(
                {
                    "worker_id": worker.worker_id,
                    "booking_id": booking.booking_id,
                    "created_at": now.isoformat(),
                }
            )[:18],
            worker_id=worker.worker_id,
            booking_id=booking.booking_id,
            listing_id=booking.listing_id,
            pool_id=booking.pool_id,
            provider_id=booking.provider_id,
            requester_wallet=booking.requester_wallet,
            hardware_type=booking.hardware_type,
            pair_symbol=booking.pair_symbol,
            paired_coin=booking.paired_coin,
            status=request.status,
            reported_hashrate=request.reported_hashrate,
            accepted_shares=request.accepted_shares,
            rejected_shares=request.rejected_shares,
            uptime_seconds=request.uptime_seconds,
            progress_percent=self.booking_progress_percent(booking, now=now),
            message=request.message,
            metrics=request.metrics,
            created_at=now,
        )
        self.put_assignment_report(report)
        return report

    def put_assignment_report(self, report: AssignmentReport) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO assignment_reports (
                    report_id,
                    worker_id,
                    booking_id,
                    listing_id,
                    provider_id,
                    pool_id,
                    status,
                    payload,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload
                """,
                (
                    report.report_id,
                    report.worker_id,
                    report.booking_id,
                    report.listing_id,
                    report.provider_id,
                    report.pool_id,
                    report.status.value,
                    report.model_dump_json(),
                    report.created_at.isoformat(),
                ),
            )

    def list_assignment_reports(
        self,
        *,
        worker_id: str | None = None,
        booking_id: str | None = None,
        limit: int = 100,
    ) -> list[AssignmentReport]:
        safe_limit = max(1, min(int(limit), 1000))
        params: list[object] = []
        query = "SELECT payload FROM assignment_reports"
        clauses: list[str] = []
        if worker_id:
            clauses.append("worker_id = ?")
            params.append(worker_id.strip())
        if booking_id:
            clauses.append("booking_id = ?")
            params.append(booking_id.strip())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [AssignmentReport.model_validate(json.loads(row["payload"])) for row in rows]

    def latest_assignment_report(
        self,
        *,
        booking_id: str,
    ) -> AssignmentReport | None:
        reports = self.list_assignment_reports(booking_id=booking_id, limit=1)
        return reports[0] if reports else None

    def expire_stale_workers(self, *, stale_after_seconds: int = 120, limit: int = 1000) -> dict[str, object]:
        cutoff_seconds = max(0, int(stale_after_seconds))
        now = utc_now()
        expired: list[dict[str, object]] = []
        for worker in self.list_workers(status=WorkerStatus.ONLINE, limit=limit):
            last_seen = worker.last_seen_at or worker.updated_at
            if (now - last_seen).total_seconds() <= cutoff_seconds:
                continue
            listing = self.get_listing(worker.listing_id)
            worker.status = WorkerStatus.OFFLINE
            worker.updated_at = now
            listing.status = ListingStatus.PAUSED
            listing.units_available = 0
            listing.updated_at = now
            listing.metadata = {
                **listing.metadata,
                "worker_status": WorkerStatus.OFFLINE.value,
                "worker_last_seen_at": last_seen.isoformat(),
                "worker_offline_at": now.isoformat(),
            }
            self.put_listing(listing)
            self.put_worker(worker)
            expired.append(
                {
                    "worker_id": worker.worker_id,
                    "listing_id": listing.listing_id,
                    "last_seen_at": last_seen.isoformat(),
                }
            )
        return {
            "checked_at": now.isoformat(),
            "stale_after_seconds": cutoff_seconds,
            "expired": len(expired),
            "workers": expired,
        }

    def put_worker(self, worker: Worker) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO workers (
                    worker_id,
                    listing_id,
                    provider_id,
                    pool_id,
                    hardware_type,
                    status,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    listing_id = excluded.listing_id,
                    provider_id = excluded.provider_id,
                    pool_id = excluded.pool_id,
                    hardware_type = excluded.hardware_type,
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    worker.worker_id,
                    worker.listing_id,
                    worker.provider_id,
                    worker.pool_id,
                    worker.hardware_type.value,
                    worker.status.value,
                    worker.model_dump_json(),
                    worker.updated_at.isoformat(),
                ),
            )

    def reserved_units_for_listing(self, listing_id: str) -> int:
        with self.storage.connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM bookings WHERE listing_id = ? AND status IN (?, ?)",
                (
                    listing_id,
                    BookingStatus.AWAITING_PAYMENT.value,
                    BookingStatus.ACTIVE.value,
                ),
            ).fetchall()
        return sum(Booking.model_validate(json.loads(row["payload"])).units for row in rows)

    def create_booking(self, request: BookingCreateRequest) -> tuple[Booking, PaymentOrder]:
        listing = self.select_listing(request)
        if request.duration_minutes < listing.min_booking_minutes:
            raise ValueError(f"duration_minutes below listing minimum {listing.min_booking_minutes}")
        if listing.units_available < request.units:
            raise ValueError("not enough available units on selected listing")
        now = utc_now()
        quote = quote_for_listing(listing, request.units, request.duration_minutes)
        payment_chain = self.get_chain(request.payment_chain_code)
        payment_token = self.get_token(payment_chain.chain_code, request.payment_token_symbol)
        if payment_token.pico_rate is None:
            raise ValueError("payment token has no PICO rate configured")
        if request.account_id:
            self.get_account(request.account_id)
        booking_id = "booking_" + hash_json(
            {
                "pool_id": listing.pool_id,
                "listing_id": listing.listing_id,
                "requester_wallet": request.requester_wallet.strip().upper(),
                "units": request.units,
                "duration_minutes": request.duration_minutes,
                "created_at": now.isoformat(),
            }
        )[:18]
        payment_id = "payment_" + hash_json(
            {
                "booking_id": booking_id,
                "chain_code": payment_chain.chain_code,
                "token_symbol": payment_token.token_symbol,
            }
        )[:18]
        payment = PaymentOrder(
            payment_id=payment_id,
            booking_id=booking_id,
            currency=payment_token.token_symbol,
            amount_pi=quote.amount_pi,
            amount_base_units=str(amount_pi_to_base_units(quote.amount_pi, payment_token)),
            payment_chain_code=payment_chain.chain_code,
            payment_token_symbol=payment_token.token_symbol,
            account_id=request.account_id,
            pay_to_address=payment_chain.deposit_address or marketplace_escrow_address(),
            memo=booking_id,
            created_at=now,
            updated_at=now,
        )
        booking = Booking(
            booking_id=booking_id,
            pool_id=listing.pool_id,
            pair_symbol=listing.pair_symbol,
            paired_coin=listing.paired_coin,
            picoin_capacity_percent=listing.picoin_capacity_percent,
            paired_capacity_percent=listing.paired_capacity_percent,
            listing_id=listing.listing_id,
            provider_id=listing.provider_id,
            provider_wallet=listing.provider_wallet,
            requester_wallet=request.requester_wallet.strip().upper(),
            hardware_type=listing.hardware_type,
            units=request.units,
            duration_minutes=request.duration_minutes,
            amount_pi=quote.amount_pi,
            picoin_capacity_units=quote.picoin_capacity_units,
            paired_capacity_units=quote.paired_capacity_units,
            payment_id=payment.payment_id,
            expires_at=now + booking_payment_window(),
            created_at=now,
            updated_at=now,
            metadata=request.metadata,
        )
        listing.units_available -= request.units
        listing.updated_at = now
        self.put_listing(listing)
        self.put_booking(booking)
        self.put_payment(payment)
        return booking, payment

    def quote_booking(self, request: BookingQuoteRequest) -> BookingQuote:
        selector = BookingCreateRequest(
            requester_wallet=request.requester_wallet,
            hardware_type=request.hardware_type,
            pool_id=request.pool_id,
            paired_coin=request.paired_coin,
            units=request.units,
            duration_minutes=request.duration_minutes,
            required_capabilities=request.required_capabilities,
            preferred_region=request.preferred_region,
            provider_id=request.provider_id,
            listing_id=request.listing_id,
        )
        listing = self.select_listing(selector)
        if request.duration_minutes < listing.min_booking_minutes:
            raise ValueError(f"duration_minutes below listing minimum {listing.min_booking_minutes}")
        return quote_for_listing(listing, request.units, request.duration_minutes)

    def select_listing(self, request: BookingCreateRequest) -> Listing:
        required = set(normalize_capabilities(request.required_capabilities))
        candidates: list[Listing]
        if request.listing_id:
            candidates = [self.get_listing(request.listing_id)]
        elif request.pool_id:
            candidates = self.list_listings(pool_id=request.pool_id, active_only=True, limit=1000)
        else:
            if request.hardware_type is None:
                raise ValueError("hardware_type is required when listing_id or pool_id is not provided")
            candidates = self.list_listings(hardware_type=request.hardware_type, active_only=True, limit=1000)
        paired_coin = normalize_coin(request.paired_coin) if request.paired_coin else None
        matches = []
        for listing in candidates:
            if listing.status != ListingStatus.ACTIVE:
                continue
            if request.hardware_type is not None and listing.hardware_type != request.hardware_type:
                continue
            if request.pool_id and listing.pool_id != request.pool_id:
                continue
            if paired_coin and listing.paired_coin != paired_coin:
                continue
            if request.provider_id and listing.provider_id != request.provider_id:
                continue
            if request.preferred_region and listing.region != request.preferred_region:
                continue
            if listing.units_available < request.units:
                continue
            if required and not required.issubset(set(normalize_capabilities(listing.capabilities))):
                continue
            matches.append(listing)
        if not matches:
            raise LookupError("no matching capacity listing available")
        return sorted(matches, key=lambda row: (row.price_pi_per_hour, row.created_at, row.listing_id))[0]

    def get_booking(self, booking_id: str) -> Booking:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM bookings WHERE booking_id = ?", (booking_id,)).fetchone()
        if row is None:
            raise KeyError(f"booking not found: {booking_id}")
        return Booking.model_validate(json.loads(row["payload"]))

    def list_bookings(self, *, requester_wallet: str | None = None, limit: int = 100) -> list[Booking]:
        safe_limit = max(1, min(int(limit), 1000))
        params: list[object] = []
        query = "SELECT payload FROM bookings"
        if requester_wallet:
            query += " WHERE requester_wallet = ?"
            params.append(requester_wallet.strip().upper())
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [Booking.model_validate(json.loads(row["payload"])) for row in rows]

    def put_booking(self, booking: Booking) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO bookings (
                    booking_id,
                    pool_id,
                    listing_id,
                    provider_id,
                    requester_wallet,
                    hardware_type,
                    status,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(booking_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    booking.booking_id,
                    booking.pool_id,
                    booking.listing_id,
                    booking.provider_id,
                    booking.requester_wallet,
                    booking.hardware_type.value,
                    booking.status.value,
                    booking.model_dump_json(),
                    booking.updated_at.isoformat(),
                ),
            )

    def get_payment(self, payment_id: str) -> PaymentOrder:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        if row is None:
            raise KeyError(f"payment not found: {payment_id}")
        return PaymentOrder.model_validate(json.loads(row["payload"]))

    def put_payment(self, payment: PaymentOrder) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO payments (
                    payment_id,
                    booking_id,
                    status,
                    tx_hash,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(payment_id) DO UPDATE SET
                    status = excluded.status,
                    tx_hash = excluded.tx_hash,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    payment.payment_id,
                    payment.booking_id,
                    payment.status.value,
                    payment.tx_hash,
                    payment.model_dump_json(),
                    payment.updated_at.isoformat(),
                ),
            )

    def submit_payment(self, payment_id: str, request: PaymentSubmitRequest) -> tuple[Booking, PaymentOrder]:
        payment = self.get_payment(payment_id)
        booking = self.get_booking(payment.booking_id)
        now = utc_now()
        payment.tx_hash = request.tx_hash
        payment.confirmations = request.confirmations
        payment.status = (
            PaymentStatus.CONFIRMED
            if request.confirmations >= marketplace_confirmations_required()
            else PaymentStatus.SUBMITTED
        )
        payment.updated_at = now
        if payment.status == PaymentStatus.CONFIRMED and booking.status == BookingStatus.AWAITING_PAYMENT:
            booking.status = BookingStatus.ACTIVE
            booking.starts_at = now
            booking.expires_at = now + timedelta(minutes=booking.duration_minutes)
            booking.updated_at = now
            self.put_booking(booking)
        self.put_payment(payment)
        return booking, payment

    def release_booking(self, booking_id: str) -> Booking:
        booking = self.get_booking(booking_id)
        if booking.status in {BookingStatus.RELEASED, BookingStatus.CANCELED, BookingStatus.EXPIRED}:
            return booking
        listing = self.get_listing(booking.listing_id)
        listing.units_available = min(listing.units_total, listing.units_available + booking.units)
        listing.updated_at = utc_now()
        booking.status = BookingStatus.RELEASED
        booking.updated_at = listing.updated_at
        self.put_listing(listing)
        self.put_booking(booking)
        return booking

    def settle_booking(self, booking_id: str) -> ProviderSettlement:
        booking = self.get_booking(booking_id)
        payment = self.get_payment(booking.payment_id)
        if payment.status != PaymentStatus.CONFIRMED:
            raise ValueError("booking payment is not confirmed")
        if booking.status not in {BookingStatus.ACTIVE, BookingStatus.RELEASED}:
            raise ValueError("booking is not active or released")
        existing = self.get_settlement_by_booking(booking.booking_id)
        if existing is not None:
            return existing
        gross_units = pi_amount_to_base_units(booking.amount_pi)
        fee_percent = marketplace_fee_percent()
        fee_units = int(
            (Decimal(gross_units) * Decimal(str(fee_percent)) / Decimal("100")).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        provider_units = max(0, gross_units - fee_units)
        now = utc_now()
        settlement = ProviderSettlement(
            settlement_id="settlement_" + hash_json({"booking_id": booking.booking_id})[:18],
            booking_id=booking.booking_id,
            payment_id=booking.payment_id,
            provider_id=booking.provider_id,
            provider_wallet=booking.provider_wallet,
            listing_id=booking.listing_id,
            pool_id=booking.pool_id,
            pair_symbol=booking.pair_symbol,
            gross_amount_pi=booking.amount_pi,
            fee_percent=fee_percent,
            fee_amount_pi=base_units_to_pi(fee_units),
            provider_amount_pi=base_units_to_pi(provider_units),
            gross_amount_base_units=str(gross_units),
            fee_amount_base_units=str(fee_units),
            provider_amount_base_units=str(provider_units),
            created_at=now,
            updated_at=now,
            metadata={
                "payment_chain_code": payment.payment_chain_code,
                "payment_token_symbol": payment.payment_token_symbol,
                "payment_amount_base_units": payment.amount_base_units,
            },
        )
        self.put_settlement(settlement)
        return settlement

    def get_settlement(self, settlement_id: str) -> ProviderSettlement:
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM provider_settlements WHERE settlement_id = ?",
                (settlement_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"settlement not found: {settlement_id}")
        return ProviderSettlement.model_validate(json.loads(row["payload"]))

    def get_settlement_by_booking(self, booking_id: str) -> ProviderSettlement | None:
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM provider_settlements WHERE booking_id = ?",
                (booking_id,),
            ).fetchone()
        if row is None:
            return None
        return ProviderSettlement.model_validate(json.loads(row["payload"]))

    def list_settlements(
        self,
        *,
        provider_id: str | None = None,
        status: SettlementStatus | None = None,
        limit: int = 100,
    ) -> list[ProviderSettlement]:
        safe_limit = max(1, min(int(limit), 1000))
        query = "SELECT payload FROM provider_settlements"
        clauses: list[str] = []
        params: list[object] = []
        if provider_id:
            clauses.append("provider_id = ?")
            params.append(provider_id.strip())
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [ProviderSettlement.model_validate(json.loads(row["payload"])) for row in rows]

    def put_settlement(self, settlement: ProviderSettlement) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO provider_settlements (
                    settlement_id,
                    booking_id,
                    payment_id,
                    provider_id,
                    provider_wallet,
                    status,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(booking_id) DO NOTHING
                """,
                (
                    settlement.settlement_id,
                    settlement.booking_id,
                    settlement.payment_id,
                    settlement.provider_id,
                    settlement.provider_wallet,
                    settlement.status.value,
                    settlement.model_dump_json(),
                    settlement.updated_at.isoformat(),
                ),
            )

    def summary(self) -> MarketplaceSummary:
        pools = self.list_pools(active_only=False, limit=1000)
        listings = self.list_listings(active_only=False, limit=1000)
        bookings = self.list_bookings(limit=1000)
        total_units: dict[str, int] = {item.value: 0 for item in HardwareType}
        available_units: dict[str, int] = {item.value: 0 for item in HardwareType}
        booked_units: dict[str, int] = {item.value: 0 for item in HardwareType}
        for listing in listings:
            total_units[listing.hardware_type.value] += listing.units_total
            available_units[listing.hardware_type.value] += listing.units_available
        for booking in bookings:
            if booking.status in {BookingStatus.AWAITING_PAYMENT, BookingStatus.ACTIVE}:
                booked_units[booking.hardware_type.value] += booking.units
        return MarketplaceSummary(
            listing_count=len(listings),
            active_listing_count=sum(1 for listing in listings if listing.status == ListingStatus.ACTIVE),
            booking_count=len(bookings),
            active_booking_count=sum(1 for booking in bookings if booking.status == BookingStatus.ACTIVE),
            total_units_by_hardware=total_units,
            available_units_by_hardware=available_units,
            booked_units_by_hardware=booked_units,
            pool_count=len(pools),
            active_pool_count=sum(1 for pool in pools if pool.status == PoolStatus.ACTIVE),
            active_pairs=sorted({pool.pair_symbol for pool in pools if pool.status == PoolStatus.ACTIVE}),
        )


def normalize_capabilities(values: list[str]) -> list[str]:
    return sorted({value.strip().lower() for value in values if value.strip()})


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if "@" not in email:
        raise ValueError("email must contain @")
    return email


def normalize_worker_id(value: str) -> str:
    worker_id = value.strip()
    if not worker_id:
        raise ValueError("worker_id is required")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{3,80}", worker_id):
        raise ValueError("worker_id may contain letters, numbers, '.', '_', ':', or '-'")
    return worker_id


def normalize_chain_code(value: str) -> str:
    return value.strip().lower()


def normalize_coin(value: str) -> str:
    return value.strip().upper()


def normalize_optional_address(family: ChainFamily, value: str | None) -> str | None:
    if not value:
        return None
    return normalize_address(family, value)


def normalize_address(family: ChainFamily, value: str) -> str:
    address = value.strip()
    if family == ChainFamily.EVM:
        if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
            raise ValueError("invalid EVM address")
        return address.lower()
    if family == ChainFamily.PICOIN:
        normalized = address.upper()
        if not normalized.startswith("PI") or len(normalized) < 10:
            raise ValueError("invalid Picoin address")
        return normalized
    raise ValueError("unsupported chain family")


def normalize_tx_hash(family: ChainFamily, value: str | None) -> str:
    if value is None:
        raise ValueError("tx hash is required")
    tx_hash = value.strip()
    if family == ChainFamily.EVM:
        if not re.fullmatch(r"0x[a-fA-F0-9]{64}", tx_hash):
            raise ValueError("invalid EVM transaction hash")
        return tx_hash.lower()
    if not re.fullmatch(r"(0x)?[a-fA-F0-9]{16,128}", tx_hash):
        raise ValueError("invalid Picoin transaction hash")
    return tx_hash.lower()


def assert_positive_base_units(value: str) -> str:
    try:
        amount = int(value)
    except ValueError as exc:
        raise ValueError("amount_base_units must be an integer string") from exc
    if amount <= 0:
        raise ValueError("amount_base_units must be greater than zero")
    return str(amount)


def deposits_match(left: Deposit, right: Deposit) -> bool:
    return (
        left.account_id == right.account_id
        and left.wallet_id == right.wallet_id
        and left.chain_code == right.chain_code
        and left.token_symbol == right.token_symbol
        and left.from_address == right.from_address
        and left.to_address == right.to_address
        and left.amount_base_units == right.amount_base_units
        and left.tx_hash == right.tx_hash
        and left.block_number == right.block_number
        and left.log_index == right.log_index
    )


def picoin_history_row_to_deposit_request(
    row: dict[str, object],
    deposit_address: str | None,
) -> ScannerDepositCreateRequest | None:
    tx_hash = first_text(row, "tx_hash", "hash", "transaction_hash", "related_id")
    sender = first_text(row, "sender", "from_address", "from")
    recipient = first_text(row, "recipient", "to_address", "to")
    status = first_text(row, "status")
    block_height = first_int(row, "block_height", "height", "confirmed_block_height")
    log_index = first_int(row, "log_index", "output_index", "index") or 0
    amount_units = first_int(row, "amount_units", "amount_base_units")
    if amount_units is None:
        amount_text = first_text(row, "amount")
        if amount_text:
            amount_units = int((Decimal(amount_text) * Decimal(1_000_000)).to_integral_value())
    if not tx_hash or not sender or not recipient or not amount_units or not block_height:
        return None
    if block_height <= 0:
        return None
    if status and status.lower() not in {"confirmed", "credited"}:
        return None
    if deposit_address and recipient.upper() != deposit_address.upper():
        return None
    return ScannerDepositCreateRequest(
        chain_code="picoin",
        token_symbol=PICO_CURRENCY,
        from_address=sender,
        to_address=recipient,
        amount_base_units=str(amount_units),
        tx_hash=tx_hash,
        block_number=block_height,
        block_hash=first_text(row, "block_hash"),
        log_index=log_index,
        status=DepositStatus.DETECTED,
    )


def latest_block_height_from_history(rows: list[dict[str, object]]) -> int | None:
    heights = [height for row in rows if (height := first_int(row, "block_height", "height"))]
    return max(heights) if heights else None


def evm_transfer_log_to_deposit_request(
    log: dict[str, object],
    chain: ChainDefinition,
    token: TokenDefinition,
) -> ScannerDepositCreateRequest | None:
    contract = normalize_address(chain.family, str(log.get("address") or ""))
    if contract != token.contract_address:
        return None
    topics = log.get("topics")
    if not isinstance(topics, list) or len(topics) < 3:
        return None
    topic0 = str(topics[0]).lower()
    if topic0 != EVM_TRANSFER_TOPIC:
        return None
    from_address = evm_topic_to_address(str(topics[1]))
    to_address = evm_topic_to_address(str(topics[2]))
    if chain.deposit_address and to_address != chain.deposit_address:
        return None
    amount = int_from_hex(str(log.get("data") or "0x0"))
    if amount <= 0:
        return None
    block_number = int_from_hex_or_int(log.get("blockNumber"))
    if block_number <= 0:
        return None
    log_index = int_from_hex_or_int(log.get("logIndex"))
    return ScannerDepositCreateRequest(
        chain_code=chain.chain_code,
        token_symbol=token.token_symbol,
        from_address=from_address,
        to_address=to_address,
        amount_base_units=str(amount),
        tx_hash=str(log.get("transactionHash") or ""),
        block_number=block_number,
        block_hash=str(log.get("blockHash")) if log.get("blockHash") else None,
        log_index=log_index,
        status=DepositStatus.DETECTED,
    )


def evm_native_tx_to_deposit_request(
    tx: dict[str, object],
    block: dict[str, object],
    chain: ChainDefinition,
    token: TokenDefinition,
    tx_index: int,
) -> ScannerDepositCreateRequest | None:
    to_value = tx.get("to")
    if not to_value:
        return None
    to_address = normalize_address(chain.family, str(to_value))
    if chain.deposit_address and to_address != chain.deposit_address:
        return None
    amount = int_from_hex_or_int(tx.get("value"))
    if amount <= 0:
        return None
    block_number = int_from_hex_or_int(tx.get("blockNumber") or block.get("number"))
    if block_number <= 0:
        return None
    from_address = normalize_address(chain.family, str(tx.get("from") or ""))
    native_log_index = EVM_NATIVE_LOG_INDEX - max(0, int(tx_index))
    return ScannerDepositCreateRequest(
        chain_code=chain.chain_code,
        token_symbol=token.token_symbol,
        from_address=from_address,
        to_address=to_address,
        amount_base_units=str(amount),
        tx_hash=str(tx.get("hash") or ""),
        block_number=block_number,
        block_hash=str(tx.get("blockHash") or block.get("hash")) if (tx.get("blockHash") or block.get("hash")) else None,
        log_index=native_log_index,
        status=DepositStatus.DETECTED,
    )


def evm_topic_to_address(topic: str) -> str:
    normalized = topic.lower()
    if normalized.startswith("0x"):
        normalized = normalized[2:]
    if len(normalized) != 64:
        raise ValueError("invalid EVM address topic")
    return "0x" + normalized[-40:]


def int_from_hex_or_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    text = str(value)
    return int_from_hex(text) if text.lower().startswith("0x") else int(text)


def int_from_hex(value: str) -> int:
    return int(value, 16)


def address_to_topic(address: str) -> str:
    normalized = address.lower()
    if normalized.startswith("0x"):
        normalized = normalized[2:]
    if len(normalized) != 40:
        raise ValueError("invalid EVM address")
    return "0x" + ("0" * 24) + normalized


def resolve_rpc_url(chain: ChainDefinition, explicit_rpc_url: str | None) -> str:
    if explicit_rpc_url:
        return explicit_rpc_url.rstrip("/")
    if chain.rpc_endpoints:
        return chain.rpc_endpoints[0].rstrip("/")
    raise ValueError("rpc_url is required when chain has no RPC endpoint configured")


def resolve_scan_from_block(
    *,
    explicit_from: int | None,
    checkpoint: int | None,
    latest: int,
    batch_size: int,
) -> int:
    if explicit_from is not None:
        return explicit_from
    if checkpoint is not None:
        return checkpoint + 1
    return max(0, latest - batch_size + 1)


def evm_token_scanner_id(chain_code: str, token_symbol: str) -> str:
    return f"evm_erc20:{normalize_chain_code(chain_code)}:{normalize_coin(token_symbol)}"


def evm_native_scanner_id(chain_code: str, token_symbol: str) -> str:
    return f"evm_native:{normalize_chain_code(chain_code)}:{normalize_coin(token_symbol)}"


def evm_block_number(rpc_url: str) -> int:
    return int_from_hex(str(json_rpc_call(rpc_url, "eth_blockNumber", [])))


def evm_get_transfer_logs(
    rpc_url: str,
    contract_address: str,
    deposit_address: str,
    from_block: int,
    to_block: int,
) -> list[dict[str, object]]:
    if not contract_address:
        raise ValueError("contract_address is required")
    if not deposit_address:
        raise ValueError("deposit address is required")
    result = json_rpc_call(
        rpc_url,
        "eth_getLogs",
        [
            {
                "address": contract_address,
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "topics": [EVM_TRANSFER_TOPIC, None, address_to_topic(deposit_address)],
            }
        ],
    )
    if not isinstance(result, list):
        raise ValueError("eth_getLogs did not return a list")
    return [row for row in result if isinstance(row, dict)]


def evm_get_block(rpc_url: str, height: int) -> dict[str, object]:
    result = json_rpc_call(rpc_url, "eth_getBlockByNumber", [hex(height), True])
    if not isinstance(result, dict):
        raise ValueError("eth_getBlockByNumber did not return a block object")
    return result


def json_rpc_call(rpc_url: str, method: str, params: list[object]) -> object:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
    ).encode("utf-8")
    timeout = float(os.getenv("PICOIN_MARKETPLACE_HTTP_TIMEOUT_SECONDS", "30"))
    request = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("error"):
        raise ValueError(f"JSON-RPC error: {data['error']}")
    return data.get("result")


def first_text(row: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def first_int(row: dict[str, object], *keys: str) -> int | None:
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def fetch_json_url(url: str) -> object:
    timeout = float(os.getenv("PICOIN_MARKETPLACE_HTTP_TIMEOUT_SECONDS", "30"))
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def split_env_list(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def evm_escrow_address() -> str:
    return os.getenv("PICOIN_MARKETPLACE_EVM_ESCROW_ADDRESS", DEFAULT_EVM_ESCROW_ADDRESS)


def pool_id_for(hardware_type: HardwareType, paired_coin: str) -> str:
    return "pool_" + hash_json(
        {
            "hardware_type": hardware_type.value,
            "paired_coin": normalize_coin(paired_coin),
        }
    )[:18]


def validate_capacity_split(picoin_percent: float, paired_percent: float) -> None:
    total = round(float(picoin_percent) + float(paired_percent), 8)
    if total != 100.0:
        raise ValueError("pool capacity split must total 100%")


def compute_amount_pi(price_pi_per_hour: float, units: int, duration_minutes: int) -> float:
    return round(float(price_pi_per_hour) * int(units) * (int(duration_minutes) / 60.0), 6)


def amount_pi_to_base_units(amount_pi: float, token: TokenDefinition) -> int:
    if token.pico_rate is None or token.pico_rate <= 0:
        raise ValueError("token has no PICO rate configured")
    token_amount = Decimal(str(amount_pi)) / Decimal(str(token.pico_rate))
    scale = Decimal(10) ** int(token.decimals)
    return int((token_amount * scale).to_integral_value(rounding=ROUND_CEILING))


def format_base_units(amount_base_units: int, decimals: int) -> str:
    scale = Decimal(10) ** int(decimals)
    amount = Decimal(int(amount_base_units)) / scale
    return format(amount.normalize(), "f")


def pi_amount_to_base_units(amount_pi: float) -> int:
    return int((Decimal(str(amount_pi)) * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))


def base_units_to_pi(amount_base_units: int) -> float:
    return float(Decimal(int(amount_base_units)) / Decimal(1_000_000))


def quote_for_listing(listing: Listing, units: int, duration_minutes: int) -> BookingQuote:
    amount_pi = compute_amount_pi(listing.price_pi_per_hour, units, duration_minutes)
    picoin_units = round(units * (listing.picoin_capacity_percent / 100.0), 8)
    paired_units = round(units * (listing.paired_capacity_percent / 100.0), 8)
    return BookingQuote(
        pool_id=listing.pool_id,
        listing_id=listing.listing_id,
        provider_id=listing.provider_id,
        hardware_type=listing.hardware_type,
        pair_symbol=listing.pair_symbol,
        paired_coin=listing.paired_coin,
        units=units,
        duration_minutes=duration_minutes,
        price_pi_per_hour=listing.price_pi_per_hour,
        amount_pi=amount_pi,
        picoin_capacity_percent=listing.picoin_capacity_percent,
        paired_capacity_percent=listing.paired_capacity_percent,
        picoin_capacity_units=picoin_units,
        paired_capacity_units=paired_units,
        available_units_after_quote=listing.units_available - units,
        can_book=listing.units_available >= units,
    )


def marketplace_escrow_address() -> str:
    return os.getenv("PICOIN_MARKETPLACE_ESCROW_ADDRESS", DEFAULT_ESCROW_ADDRESS)


def marketplace_confirmations_required() -> int:
    try:
        value = int(os.getenv("PICOIN_MARKETPLACE_CONFIRMATIONS_REQUIRED", str(DEFAULT_CONFIRMATIONS_REQUIRED)))
    except ValueError:
        return DEFAULT_CONFIRMATIONS_REQUIRED
    return max(1, value)


def marketplace_fee_percent() -> float:
    try:
        value = float(os.getenv("PICOIN_MARKETPLACE_FEE_PERCENT", "1"))
    except ValueError:
        value = 1.0
    return max(0.0, min(value, 100.0))


def booking_payment_window() -> timedelta:
    try:
        minutes = int(os.getenv("PICOIN_MARKETPLACE_PAYMENT_WINDOW_MINUTES", "30"))
    except ValueError:
        minutes = 30
    return timedelta(minutes=max(1, minutes))
