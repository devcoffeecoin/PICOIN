from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .marketplace import Marketplace, DEFAULT_STATE_DIR
from .models import (
    AccountCreateRequest,
    AssignmentReportRequest,
    BookingCreateRequest,
    BookingQuoteRequest,
    ChainCreateRequest,
    ConfirmationProcessRequest,
    EvmNativeTransferPollRequest,
    EvmTokenTransferImportRequest,
    EvmTokenTransferPollRequest,
    HardwareType,
    ListingCreateRequest,
    MiningPoolCreateRequest,
    PayFromBalanceRequest,
    PaymentSubmitRequest,
    PicoinHistoryImportRequest,
    PicoinNodePollRequest,
    ScannerDepositCreateRequest,
    SettlementStatus,
    TokenCreateRequest,
    WalletCreateRequest,
    WorkerHeartbeatRequest,
    WorkerRegisterRequest,
    WorkerStatus,
)


api = FastAPI(title="Picoin Compute Marketplace", version="0.1.0")


def marketplace() -> Marketplace:
    instance = Marketplace(Path(os.getenv("PICOIN_MARKETPLACE_STATE_DIR", str(DEFAULT_STATE_DIR))))
    if os.getenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "1").lower() not in {"0", "false", "no"}:
        instance.ensure_default_pools()
    return instance


@api.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "picoin-marketplace",
        "domain": "marketplace.picoin.science",
        "currency": "PICO",
    }


@api.post("/accounts")
def create_account_api(payload: AccountCreateRequest) -> dict:
    try:
        return marketplace().create_account(payload).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/accounts")
def accounts_api(limit: int = 100) -> list[dict]:
    return [account.model_dump(mode="json") for account in marketplace().list_accounts(limit=limit)]


@api.get("/accounts/{account_id}")
def account_api(account_id: str) -> dict:
    try:
        return marketplace().get_account(account_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/accounts/{account_id}/wallets")
def create_wallet_api(account_id: str, payload: WalletCreateRequest) -> dict:
    try:
        return marketplace().create_wallet(account_id, payload).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/wallets")
def wallets_api(account_id: str | None = None, limit: int = 100) -> list[dict]:
    return [
        wallet.model_dump(mode="json")
        for wallet in marketplace().list_wallets(account_id=account_id, limit=limit)
    ]


@api.post("/wallets/{wallet_id}/verify")
def verify_wallet_api(wallet_id: str) -> dict:
    try:
        return marketplace().verify_wallet(wallet_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/chains")
def create_chain_api(payload: ChainCreateRequest) -> dict:
    try:
        return marketplace().create_chain(payload).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/chains")
def chains_api(enabled_only: bool = True) -> list[dict]:
    return [chain.model_dump(mode="json") for chain in marketplace().list_chains(enabled_only=enabled_only)]


@api.post("/tokens")
def create_token_api(payload: TokenCreateRequest) -> dict:
    try:
        return marketplace().create_token(payload).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/tokens")
def tokens_api(chain_code: str | None = None, enabled_only: bool = True) -> list[dict]:
    return [
        token.model_dump(mode="json")
        for token in marketplace().list_tokens(chain_code=chain_code, enabled_only=enabled_only)
    ]


@api.get("/scanner/{chain_code}/config")
def scanner_config_api(chain_code: str) -> dict:
    try:
        return marketplace().scanner_config(chain_code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/scanner/deposits")
def scanner_deposit_api(payload: ScannerDepositCreateRequest) -> dict:
    try:
        return marketplace().accept_deposit(payload).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/scanner/{chain_code}/confirmations/process")
def process_confirmations_api(chain_code: str, payload: ConfirmationProcessRequest) -> dict:
    try:
        return marketplace().process_confirmations(chain_code, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/scanner/picoin/import-history")
def import_picoin_history_api(payload: PicoinHistoryImportRequest) -> dict:
    return marketplace().import_picoin_history(payload)


@api.post("/scanner/picoin/poll")
def poll_picoin_node_api(payload: PicoinNodePollRequest) -> dict:
    try:
        return marketplace().poll_picoin_node(payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@api.post("/scanner/evm/import-token-transfers")
def import_evm_token_transfers_api(payload: EvmTokenTransferImportRequest) -> dict:
    try:
        return marketplace().import_evm_token_transfers(payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/scanner/evm/poll-token-transfers")
def poll_evm_token_transfers_api(payload: EvmTokenTransferPollRequest) -> dict:
    try:
        return marketplace().poll_evm_token_transfers(payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@api.post("/scanner/evm/poll-native-transfers")
def poll_evm_native_transfers_api(payload: EvmNativeTransferPollRequest) -> dict:
    try:
        return marketplace().poll_evm_native_transfers(payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@api.get("/deposits")
def deposits_api(account_id: str | None = None, limit: int = 100) -> list[dict]:
    return [
        deposit.model_dump(mode="json")
        for deposit in marketplace().list_deposits(account_id=account_id, limit=limit)
    ]


@api.get("/ledger")
def ledger_api(account_id: str | None = None, limit: int = 100) -> list[dict]:
    return [
        entry.model_dump(mode="json")
        for entry in marketplace().list_ledger(account_id=account_id, limit=limit)
    ]


@api.get("/accounts/{account_id}/balances")
def account_balances_api(account_id: str) -> list[dict]:
    try:
        return [balance.model_dump(mode="json") for balance in marketplace().account_balances(account_id)]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.get("/", response_class=HTMLResponse)
def home() -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Picoin Marketplace</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3f6f8;
      --surface: #ffffff;
      --line: #d6dde2;
      --ink: #17212b;
      --muted: #667480;
      --accent: #14746f;
      --accent-2: #2f5d8c;
      --warn: #9a5b13;
      --danger: #9c2f2f;
      --soft: #edf5f4;
      --blue-soft: #edf3fb;
      --gold-soft: #fbf4e6;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--bg);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    header {{
      background: var(--surface);
      border-bottom: 1px solid var(--line);
      padding: 13px 22px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      position: sticky;
      top: 0;
      z-index: 2;
    }}
    h1 {{ margin: 0; font-size: 1.16rem; font-weight: 760; }}
    h2 {{ margin: 0; font-size: .98rem; font-weight: 760; }}
    h3 {{ margin: 0; font-size: .95rem; font-weight: 760; }}
    p {{ margin: 0; }}
    main {{
      max-width: 1360px;
      margin: 0 auto;
      padding: 18px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 16px;
    }}
    .main-column, .side-column {{
      display: grid;
      gap: 14px;
      align-content: start;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }}
    .metric, section, .pool-card {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .metric {{ padding: 12px; min-height: 76px; }}
    .metric span {{ display: block; color: var(--muted); font-size: .78rem; }}
    .metric strong {{ display: block; font-size: 1.45rem; margin-top: 6px; }}
    section {{ padding: 14px; overflow: hidden; }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 12px;
    }}
    .tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .tab {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 999px;
      padding: 7px 11px;
      font-size: .82rem;
      font-weight: 700;
    }}
    .tab.active {{
      background: var(--ink);
      color: #fff;
      border-color: var(--ink);
    }}
    .pool-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .pool-card {{
      padding: 14px;
      display: grid;
      gap: 12px;
    }}
    .pool-top {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }}
    .pair {{
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .pair strong {{ font-size: 1.16rem; }}
    .muted {{ color: var(--muted); font-size: .82rem; }}
    .pill {{
      background: var(--soft);
      color: #0d5e59;
      border: 1px solid #cce2df;
      border-radius: 999px;
      padding: 2px 7px;
      font-size: .78rem;
      white-space: nowrap;
    }}
    .pill.blue {{
      background: var(--blue-soft);
      color: #214f82;
      border-color: #c9daef;
    }}
    .pill.gold {{
      background: var(--gold-soft);
      color: #81570e;
      border-color: #ead59b;
    }}
    .split {{
      display: grid;
      gap: 6px;
    }}
    .split-bar {{
      display: grid;
      grid-template-columns: var(--pico, 10fr) var(--paired, 90fr);
      height: 10px;
      border-radius: 999px;
      overflow: hidden;
      background: #e8eef1;
    }}
    .split-bar span:first-child {{ background: var(--accent); }}
    .split-bar span:last-child {{ background: var(--accent-2); }}
    .card-stats {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }}
    .stat {{
      background: #f8fafb;
      border: 1px solid #e3e8ec;
      border-radius: 7px;
      padding: 8px;
      min-height: 58px;
    }}
    .stat span {{ color: var(--muted); font-size: .72rem; display: block; }}
    .stat strong {{ margin-top: 5px; display: block; font-size: .92rem; }}
    .card-actions {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
    }}
    .package-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin: 0 0 10px;
    }}
    .package {{
      background: #fff;
      color: var(--ink);
      border: 1px solid var(--line);
      text-align: left;
      padding: 9px;
      border-radius: 8px;
    }}
    .package.active {{
      border-color: var(--accent);
      box-shadow: 0 0 0 2px #d4ebe8;
    }}
    .package strong, .package span {{
      display: block;
    }}
    .package span {{
      color: var(--muted);
      font-size: .74rem;
      margin-top: 3px;
    }}
    .quote-box {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafb;
      padding: 10px;
      margin: 10px 0;
      display: grid;
      gap: 8px;
    }}
    .quote-row {{
      display: flex;
      justify-content: space-between;
      gap: 8px;
      font-size: .86rem;
    }}
    .quote-row strong {{
      font-size: 1rem;
    }}
    .balance-list {{
      display: grid;
      gap: 6px;
      margin-top: 8px;
    }}
    .balance-line {{
      display: flex;
      justify-content: space-between;
      gap: 8px;
      border-bottom: 1px solid #e3e8ec;
      padding: 6px 0;
      font-size: .84rem;
    }}
    .balance-line:last-child {{ border-bottom: 0; }}
    .empty-state {{
      border: 1px dashed #b8c4cc;
      border-radius: 8px;
      padding: 18px;
      color: var(--muted);
      background: #fbfcfd;
    }}
    form {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}
    label {{ display: grid; gap: 5px; color: var(--muted); font-size: .78rem; }}
    input, select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
      color: var(--ink);
      background: #fff;
    }}
    button {{
      border: 0;
      border-radius: 6px;
      padding: 10px 12px;
      background: var(--accent);
      color: white;
      font-weight: 700;
      cursor: pointer;
    }}
    button.secondary {{ background: var(--accent-2); }}
    button.light {{
      background: #fff;
      color: var(--ink);
      border: 1px solid var(--line);
    }}
    button:disabled {{
      background: #d7dee3;
      color: #7d8992;
      cursor: not-allowed;
    }}
    .span-2 {{ grid-column: span 2; }}
    .toolbar {{ display: flex; gap: 8px; align-items: center; }}
    .status {{ color: var(--muted); font-size: .86rem; }}
    pre {{
      margin: 10px 0 0;
      max-height: 220px;
      overflow: auto;
      background: #111923;
      color: #dce8ec;
      border-radius: 8px;
      padding: 10px;
      font-size: .78rem;
    }}
    .mini-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: .84rem;
    }}
    .mini-table th, .mini-table td {{
      border-bottom: 1px solid var(--line);
      padding: 8px 4px;
      text-align: left;
    }}
    .mini-table th {{ color: var(--muted); background: #f8fafb; }}
    @media (max-width: 1080px) {{
      main {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 860px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      .metrics, .pool-grid, form, .card-stats {{ grid-template-columns: 1fr; }}
      .span-2 {{ grid-column: span 1; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Picoin Marketplace</h1>
    <div class="toolbar">
      <span class="pill">PICO + Ethereum tokens</span>
      <span class="pill blue">CPU / GPU / ASIC</span>
      <span class="status">marketplace.picoin.science</span>
    </div>
  </header>
  <main>
    <div class="main-column">
      <div class="metrics">
        <div class="metric"><span>Active pools</span><strong id="metric-pools">0</strong></div>
        <div class="metric"><span>Active listings</span><strong id="metric-listings">0</strong></div>
        <div class="metric"><span>Available units</span><strong id="metric-units">0</strong></div>
        <div class="metric"><span>Currency</span><strong id="metric-currency">PICO</strong></div>
      </div>
      <section>
        <div class="section-head">
          <div>
            <h2>Easy Mining Pools</h2>
            <p class="muted">Choose a paired mining pool, reserve capacity, and pay with Picoin or approved Ethereum tokens.</p>
          </div>
          <div class="tabs" id="hardware-tabs">
            <button class="tab active" data-filter="all">All</button>
            <button class="tab" data-filter="cpu">CPU</button>
            <button class="tab" data-filter="gpu">GPU</button>
            <button class="tab" data-filter="asic">ASIC</button>
          </div>
        </div>
        <div class="pool-grid" id="pool-cards"></div>
      </section>
      <section>
        <div class="section-head">
          <h2>Live Capacity Listings</h2>
          <span class="muted" id="listing-count-label"></span>
        </div>
        <table class="mini-table">
          <thead><tr><th>Provider</th><th>Pair</th><th>Hardware</th><th>Units</th><th>PI/hour</th></tr></thead>
          <tbody id="listing-rows"></tbody>
        </table>
      </section>
      <section>
        <div class="section-head">
          <h2>Live Execution</h2>
          <span class="muted" id="report-count-label"></span>
        </div>
        <table class="mini-table">
          <thead><tr><th>Worker</th><th>Pair</th><th>Status</th><th>Progress</th><th>Hashrate</th><th>Shares</th><th>Updated</th></tr></thead>
          <tbody id="report-rows"></tbody>
        </table>
      </section>
    </div>
    <div class="side-column">
      <section>
        <div class="section-head">
          <h2>Quick Order</h2>
          <span class="pill gold">10% PICO / 90% pair</span>
        </div>
        <div class="package-grid" id="package-grid">
          <button class="package active" type="button" data-units="1" data-minutes="60"><strong>Starter</strong><span>1 unit / 1 hour</span></button>
          <button class="package" type="button" data-units="5" data-minutes="360"><strong>Boost</strong><span>5 units / 6 hours</span></button>
          <button class="package" type="button" data-units="10" data-minutes="1440"><strong>Pro</strong><span>10 units / 24 hours</span></button>
        </div>
        <form id="booking-form">
          <label class="span-2">Pool
            <select name="pool_id" id="booking-pool"></select>
          </label>
          <label class="span-2">Picoin wallet
            <input name="requester_wallet" value="PI_CUSTOMER_WALLET" required>
          </label>
          <label class="span-2">Account ID
            <input name="account_id" id="booking-account-id" placeholder="acct_..." >
          </label>
          <label>Payment chain
            <select name="payment_chain_code">
              <option value="picoin">Picoin</option>
              <option value="ethereum">Ethereum</option>
            </select>
          </label>
          <label>Payment token
            <select name="payment_token_symbol">
              <option value="PICO">PICO</option>
              <option value="ETH">ETH</option>
            </select>
          </label>
          <label>Units
            <input name="units" type="number" min="1" value="1" required>
          </label>
          <label>Duration
            <select name="duration_minutes">
              <option value="60">1 hour</option>
              <option value="360">6 hours</option>
              <option value="1440">24 hours</option>
            </select>
          </label>
          <button class="span-2" type="submit">Reserve capacity</button>
        </form>
        <div class="quote-box" id="quote-box">
          <div class="quote-row"><span>Selected pair</span><strong id="quote-pair">-</strong></div>
          <div class="quote-row"><span>Total cost</span><strong id="quote-amount">-</strong></div>
          <div class="quote-row"><span>Picoin capacity</span><strong id="quote-pico">-</strong></div>
          <div class="quote-row"><span>Paired capacity</span><strong id="quote-paired">-</strong></div>
        </div>
        <pre id="booking-output"></pre>
        <div class="card-actions" style="margin-top:10px">
          <span class="muted">Use the selected account balance for the last reservation.</span>
          <button id="pay-balance-button" type="button" class="secondary">Pay from confirmed balance</button>
        </div>
        <div class="quote-box">
          <div class="quote-row"><span>Account balances</span><button id="refresh-balances-button" class="light" type="button">Refresh</button></div>
          <div class="balance-list" id="balance-list"><span class="muted">Create or select an account to view balances.</span></div>
        </div>
      </section>
      <section>
        <div class="section-head">
          <h2>Accounts & Deposits</h2>
          <span class="pill blue">scanner ready</span>
        </div>
        <form id="account-form">
          <label class="span-2">Email
            <input name="email" value="customer@example.com" required>
          </label>
          <label class="span-2">Display name
            <input name="display_name" value="Marketplace Customer">
          </label>
          <button class="span-2 secondary" type="submit">Create account</button>
        </form>
        <form id="wallet-form" style="margin-top:10px">
          <label class="span-2">Account ID
            <input name="account_id" id="wallet-account-id" placeholder="acct_..." required>
          </label>
          <label>Chain
            <select name="chain_code">
              <option value="picoin">Picoin</option>
              <option value="ethereum">Ethereum</option>
            </select>
          </label>
          <label>Wallet
            <input name="address" value="PI_CUSTOMER_WALLET" required>
          </label>
          <button class="span-2 light" type="submit">Register and verify wallet</button>
        </form>
        <form id="deposit-form" style="margin-top:10px">
          <label>Chain
            <select name="chain_code">
              <option value="picoin">Picoin</option>
              <option value="ethereum">Ethereum</option>
            </select>
          </label>
          <label>Token
            <select name="token_symbol">
              <option value="PICO">PICO</option>
              <option value="ETH">ETH</option>
            </select>
          </label>
          <label class="span-2">From verified wallet
            <input name="from_address" value="PI_CUSTOMER_WALLET" required>
          </label>
          <label class="span-2">To marketplace deposit address
            <input name="to_address" id="deposit-to-address" value="PI_MARKETPLACE_ESCROW" required>
          </label>
          <label>Amount base units
            <input name="amount_base_units" value="10000000" required>
          </label>
          <label>Block
            <input name="block_number" type="number" value="100" required>
          </label>
          <label class="span-2">Transaction hash
            <input name="tx_hash" value="abcdef1234567890abcdef1234567890" required>
          </label>
          <button class="span-2" type="submit">Record deposit and confirm</button>
        </form>
        <pre id="account-output"></pre>
      </section>
      <section>
        <h2>Publish Capacity</h2>
        <form id="listing-form">
          <label>Pool
            <select name="pool_id" id="listing-pool"></select>
          </label>
          <label>Hardware
            <select name="hardware_type">
              <option value="cpu">CPU</option>
              <option value="gpu">GPU</option>
              <option value="asic">ASIC</option>
            </select>
          </label>
          <label>Provider
            <input name="provider_id" value="provider-1" required>
          </label>
          <label>Wallet
            <input name="provider_wallet" value="PI_PROVIDER_WALLET" required>
          </label>
          <label class="span-2">Title
            <input name="title" value="Mining capacity node" required>
          </label>
          <label>Units
            <input name="units_total" type="number" min="1" value="1" required>
          </label>
          <label>PI/hour
            <input name="price_pi_per_hour" type="number" min="0.000001" step="0.000001" value="1" required>
          </label>
          <button class="span-2 secondary" type="submit">Publish capacity</button>
        </form>
        <pre id="listing-output"></pre>
      </section>
      <section>
        <h2>Worker Agents</h2>
        <form id="worker-form">
          <label>Pool
            <select name="pool_id" id="worker-pool"></select>
          </label>
          <label>Hardware
            <select name="hardware_type">
              <option value="cpu">CPU</option>
              <option value="gpu">GPU</option>
              <option value="asic">ASIC</option>
            </select>
          </label>
          <label>Worker ID
            <input name="worker_id" value="worker-gpu-1">
          </label>
          <label>Provider
            <input name="provider_id" value="provider-gpu-1" required>
          </label>
          <label class="span-2">Provider wallet
            <input name="provider_wallet" value="PI_PROVIDER_GPU" required>
          </label>
          <label>Units
            <input name="units_total" type="number" min="1" value="3" required>
          </label>
          <label>PI/hour
            <input name="price_pi_per_hour" type="number" min="0.000001" step="0.000001" value="2" required>
          </label>
          <label class="span-2">Title
            <input name="title" value="GPU worker node">
          </label>
          <button class="span-2 secondary" type="submit">Register worker</button>
        </form>
        <form id="worker-heartbeat-form" style="margin-top:10px">
          <label>Worker ID
            <input name="worker_id" value="worker-gpu-1" required>
          </label>
          <label>Status
            <select name="status">
              <option value="online">Online</option>
              <option value="paused">Paused</option>
              <option value="offline">Offline</option>
            </select>
          </label>
          <label>Units total
            <input name="units_total" type="number" min="1" value="3">
          </label>
          <label>Units available
            <input name="units_available" type="number" min="0" value="3">
          </label>
          <button class="span-2 light" type="submit">Send heartbeat</button>
        </form>
        <table class="mini-table" style="margin-top:10px">
          <thead><tr><th>Worker</th><th>Status</th><th>Pool</th><th>Seen</th></tr></thead>
          <tbody id="worker-rows"></tbody>
        </table>
        <pre id="worker-output"></pre>
      </section>
      <section>
        <h2>Create Pair Pool</h2>
        <form id="pool-form">
          <label>Hardware
            <select name="hardware_type">
              <option value="cpu">CPU</option>
              <option value="gpu">GPU</option>
              <option value="asic">ASIC</option>
            </select>
          </label>
          <label>Paired coin
            <input name="paired_coin" value="monero" required>
          </label>
          <label>Picoin %
            <input name="picoin_capacity_percent" type="number" min="0" max="100" step="0.0001" value="10" required>
          </label>
          <label>Paired %
            <input name="paired_capacity_percent" type="number" min="0" max="100" step="0.0001" value="90" required>
          </label>
          <label class="span-2">Name
            <input name="name" value="CPU PICO/MONERO pool">
          </label>
          <button class="span-2 light" type="submit">Create pool</button>
        </form>
        <pre id="pool-output"></pre>
      </section>
    </div>
  </main>
  <script>
    const json = value => JSON.stringify(value, null, 2);
    const out = (id, value) => document.getElementById(id).textContent = typeof value === 'string' ? value : json(value);
    let activeFilter = 'all';
    let cardData = [];
    let lastPaymentId = null;
    async function request(path, options = {{}}) {{
      const response = await fetch(path, options);
      const payload = await response.json();
      if (!response.ok) throw payload;
      return payload;
    }}
    function readForm(form) {{
      const data = Object.fromEntries(new FormData(form).entries());
      for (const key of ['picoin_capacity_percent', 'paired_capacity_percent', 'units_total', 'units_available', 'price_pi_per_hour', 'units', 'duration_minutes', 'block_number', 'log_index']) {{
        if (key in data) data[key] = Number(data[key]);
      }}
      for (const key of Object.keys(data)) {{
        if (data[key] === '') delete data[key];
      }}
      return data;
    }}
    function fmtPi(value) {{
      return value === null || value === undefined ? '-' : Number(value).toFixed(6).replace(/0+$/, '').replace(/\\.$/, '');
    }}
    function setQuote(payload) {{
      document.getElementById('quote-pair').textContent = payload?.pair_symbol || '-';
      document.getElementById('quote-amount').textContent = payload ? `${{fmtPi(payload.amount_pi)}} PICO` : '-';
      document.getElementById('quote-pico').textContent = payload ? `${{fmtPi(payload.picoin_capacity_units)}} units` : '-';
      document.getElementById('quote-paired').textContent = payload ? `${{fmtPi(payload.paired_capacity_units)}} units` : '-';
    }}
    function renderBalances(rows) {{
      const target = document.getElementById('balance-list');
      if (!rows || !rows.length) {{
        target.innerHTML = '<span class="muted">No balances yet.</span>';
        return;
      }}
      target.innerHTML = rows.map(row => `
        <div class="balance-line">
          <span>${{row.chain_code}} / ${{row.token_symbol}}</span>
          <strong>${{row.available}}</strong>
        </div>
      `).join('');
    }}
    async function refreshBalances() {{
      const accountId = document.getElementById('booking-account-id').value.trim()
        || document.getElementById('wallet-account-id').value.trim();
      if (!accountId) {{
        renderBalances(null);
        return;
      }}
      try {{
        const rows = await request(`/accounts/${{accountId}}/balances`);
        renderBalances(rows);
      }} catch (error) {{
        document.getElementById('balance-list').innerHTML = `<span class="muted">${{JSON.stringify(error)}}</span>`;
      }}
    }}
    async function refreshQuote() {{
      try {{
        const payload = readForm(document.getElementById('booking-form'));
        const quote = await request('/bookings/quote', {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify(payload)
        }});
        setQuote(quote);
      }} catch (error) {{
        setQuote(null);
      }}
    }}
    function poolOption(pool) {{
      const option = document.createElement('option');
      option.value = pool.pool_id;
      option.textContent = `${{pool.pair_symbol}} - ${{pool.hardware_type.toUpperCase()}}`;
      option.dataset.hardware = pool.hardware_type;
      return option;
    }}
    function syncHardwareFromPool(select, hardwareSelect) {{
      const selected = select.options[select.selectedIndex];
      if (selected?.dataset.hardware) hardwareSelect.value = selected.dataset.hardware;
    }}
    function renderCards() {{
      const cards = cardData.filter(card => activeFilter === 'all' || card.hardware_type === activeFilter);
      const container = document.getElementById('pool-cards');
      if (!cards.length) {{
        container.innerHTML = '<div class="empty-state">No pools match this hardware filter.</div>';
        return;
      }}
      container.innerHTML = cards.map(card => `
        <article class="pool-card">
          <div class="pool-top">
            <div class="pair">
              <strong>${{card.pair_symbol}}</strong>
              <span class="muted">${{card.name}}</span>
            </div>
            <span class="pill ${{card.hardware_type === 'gpu' ? 'blue' : card.hardware_type === 'asic' ? 'gold' : ''}}">${{card.hardware_type.toUpperCase()}}</span>
          </div>
          <div class="split">
            <div class="split-bar" style="--pico:${{card.picoin_capacity_percent}}fr;--paired:${{card.paired_capacity_percent}}fr"><span></span><span></span></div>
            <span class="muted">${{card.picoin_capacity_percent}}% Picoin support / ${{card.paired_capacity_percent}}% ${{card.paired_coin}} target</span>
          </div>
          <div class="card-stats">
            <div class="stat"><span>Available</span><strong>${{card.available_units}}</strong></div>
            <div class="stat"><span>From</span><strong>${{fmtPi(card.min_price_pi_per_hour)}} PI/h</strong></div>
            <div class="stat"><span>Algorithm</span><strong>${{card.algorithm || '-'}}</strong></div>
          </div>
          <div class="card-actions">
            <span class="muted">${{card.can_book ? 'Ready for instant reservation' : 'Waiting for providers'}}</span>
            <button data-pool="${{card.pool_id}}" ${{card.can_book ? '' : 'disabled'}}>Mine this pair</button>
          </div>
        </article>
      `).join('');
      for (const button of container.querySelectorAll('button[data-pool]')) {{
        button.addEventListener('click', () => {{
          document.getElementById('booking-pool').value = button.dataset.pool;
          document.getElementById('booking-output').textContent = '';
          refreshQuote();
          document.getElementById('booking-form').scrollIntoView({{behavior:'smooth', block:'center'}});
        }});
      }}
    }}
    async function loadData() {{
      const [summary, pools, cards, listings, workers, reports] = await Promise.all([
        request('/summary'),
        request('/pools?active_only=true&limit=100'),
        request('/pool-cards?active_only=true&limit=100'),
        request('/listings?active_only=true&limit=100'),
        request('/workers?limit=100'),
        request('/assignment-reports?limit=25')
      ]);
      cardData = cards;
      document.getElementById('metric-pools').textContent = summary.active_pool_count;
      document.getElementById('metric-listings').textContent = summary.active_listing_count;
      document.getElementById('metric-units').textContent = Object.values(summary.available_units_by_hardware).reduce((a,b) => a + b, 0);
      document.getElementById('metric-currency').textContent = summary.currency;
      renderCards();
      document.getElementById('listing-count-label').textContent = `${{listings.length}} active`;
      document.getElementById('listing-rows').innerHTML = listings.map(item => `
        <tr>
          <td>${{item.provider_id}}</td>
          <td><code>${{item.pair_symbol}}</code></td>
          <td>${{item.hardware_type.toUpperCase()}}</td>
          <td>${{item.units_available}} / ${{item.units_total}}</td>
          <td>${{fmtPi(item.price_pi_per_hour)}}</td>
        </tr>`).join('');
      document.getElementById('worker-rows').innerHTML = workers.length ? workers.map(item => `
        <tr>
          <td>${{item.worker_id}}</td>
          <td>${{item.status}}</td>
          <td><code>${{item.pool_id.slice(0, 12)}}</code></td>
          <td>${{item.last_seen_at ? item.last_seen_at.replace('T', ' ').slice(0, 19) : '-'}}</td>
        </tr>`).join('') : '<tr><td colspan="4" class="muted">No workers registered yet.</td></tr>';
      document.getElementById('report-count-label').textContent = `${{reports.length}} recent`;
      document.getElementById('report-rows').innerHTML = reports.length ? reports.map(item => `
        <tr>
          <td>${{item.worker_id}}</td>
          <td><code>${{item.pair_symbol}}</code></td>
          <td>${{item.status}}</td>
          <td>${{item.progress_percent}}%</td>
          <td>${{item.reported_hashrate ?? '-'}}</td>
          <td>${{item.accepted_shares ?? 0}} / ${{item.rejected_shares ?? 0}}</td>
          <td>${{item.created_at ? item.created_at.replace('T', ' ').slice(0, 19) : '-'}}</td>
        </tr>`).join('') : '<tr><td colspan="7" class="muted">No execution reports yet.</td></tr>';
      for (const selectId of ['listing-pool', 'booking-pool', 'worker-pool']) {{
        const select = document.getElementById(selectId);
        const current = select.value;
        select.innerHTML = '';
        for (const pool of pools) select.appendChild(poolOption(pool));
        if (current) select.value = current;
      }}
      syncHardwareFromPool(document.getElementById('listing-pool'), document.querySelector('#listing-form [name="hardware_type"]'));
      syncHardwareFromPool(document.getElementById('worker-pool'), document.querySelector('#worker-form [name="hardware_type"]'));
      await refreshQuote();
    }}
    document.getElementById('package-grid').addEventListener('click', event => {{
      const button = event.target.closest('button[data-units]');
      if (!button) return;
      for (const item of document.querySelectorAll('#package-grid .package')) item.classList.toggle('active', item === button);
      document.querySelector('#booking-form [name="units"]').value = button.dataset.units;
      document.querySelector('#booking-form [name="duration_minutes"]').value = button.dataset.minutes;
      refreshQuote();
    }});
    document.getElementById('hardware-tabs').addEventListener('click', event => {{
      const button = event.target.closest('button[data-filter]');
      if (!button) return;
      activeFilter = button.dataset.filter;
      for (const item of document.querySelectorAll('#hardware-tabs .tab')) item.classList.toggle('active', item === button);
      renderCards();
    }});
    document.getElementById('listing-pool').addEventListener('change', event => {{
      syncHardwareFromPool(event.target, document.querySelector('#listing-form [name="hardware_type"]'));
    }});
    document.getElementById('worker-pool').addEventListener('change', event => {{
      syncHardwareFromPool(event.target, document.querySelector('#worker-form [name="hardware_type"]'));
    }});
    document.getElementById('booking-form').addEventListener('input', refreshQuote);
    document.getElementById('booking-form').addEventListener('change', refreshQuote);
    document.getElementById('pool-form').addEventListener('submit', async event => {{
      event.preventDefault();
      try {{
        const payload = readForm(event.target);
        const result = await request('/pools', {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify(payload)
        }});
        out('pool-output', result);
        await loadData();
      }} catch (error) {{ out('pool-output', error); }}
    }});
    document.getElementById('listing-form').addEventListener('submit', async event => {{
      event.preventDefault();
      try {{
        const payload = readForm(event.target);
        const result = await request('/listings', {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify(payload)
        }});
        out('listing-output', result);
        await loadData();
      }} catch (error) {{ out('listing-output', error); }}
    }});
    document.getElementById('worker-form').addEventListener('submit', async event => {{
      event.preventDefault();
      try {{
        const payload = readForm(event.target);
        const result = await request('/workers/register', {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify(payload)
        }});
        document.querySelector('#worker-heartbeat-form [name="worker_id"]').value = result.worker.worker_id;
        out('worker-output', result);
        await loadData();
      }} catch (error) {{ out('worker-output', error); }}
    }});
    document.getElementById('worker-heartbeat-form').addEventListener('submit', async event => {{
      event.preventDefault();
      try {{
        const payload = readForm(event.target);
        const workerId = payload.worker_id;
        delete payload.worker_id;
        const result = await request(`/workers/${{workerId}}/heartbeat`, {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify(payload)
        }});
        out('worker-output', result);
        await loadData();
      }} catch (error) {{ out('worker-output', error); }}
    }});
    document.getElementById('booking-form').addEventListener('submit', async event => {{
      event.preventDefault();
      try {{
        const payload = readForm(event.target);
        if (!payload.account_id) delete payload.account_id;
        const result = await request('/bookings', {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify(payload)
        }});
        lastPaymentId = result.payment.payment_id;
        out('booking-output', result);
        await refreshBalances();
        await loadData();
      }} catch (error) {{ out('booking-output', error); }}
    }});
    document.getElementById('account-form').addEventListener('submit', async event => {{
      event.preventDefault();
      try {{
        const account = await request('/accounts', {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify(readForm(event.target))
        }});
        document.getElementById('wallet-account-id').value = account.account_id;
        document.getElementById('booking-account-id').value = account.account_id;
        out('account-output', account);
        await refreshBalances();
      }} catch (error) {{ out('account-output', error); }}
    }});
    document.getElementById('wallet-form').addEventListener('submit', async event => {{
      event.preventDefault();
      try {{
        const payload = readForm(event.target);
        const accountId = payload.account_id;
        delete payload.account_id;
        const wallet = await request(`/accounts/${{accountId}}/wallets`, {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify(payload)
        }});
        const verified = await request(`/wallets/${{wallet.wallet_id}}/verify`, {{ method: 'POST' }});
        out('account-output', verified);
        await refreshBalances();
      }} catch (error) {{ out('account-output', error); }}
    }});
    document.getElementById('deposit-form').addEventListener('change', async event => {{
      const chain = event.currentTarget.elements.chain_code.value;
      try {{
        const config = await request(`/scanner/${{chain}}/config`);
        if (config.deposit_address) document.getElementById('deposit-to-address').value = config.deposit_address;
      }} catch (_) {{}}
    }});
    document.getElementById('deposit-form').addEventListener('submit', async event => {{
      event.preventDefault();
      try {{
        const payload = readForm(event.target);
        const deposit = await request('/scanner/deposits', {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify(payload)
        }});
        const confirmed = await request(`/scanner/${{payload.chain_code}}/confirmations/process`, {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify({{ latest_block_number: Number(payload.block_number) + 32 }})
        }});
        const accountId = deposit.account_id;
        const balances = await request(`/accounts/${{accountId}}/balances`);
        out('account-output', {{ deposit, confirmed, balances }});
        renderBalances(balances);
      }} catch (error) {{ out('account-output', error); }}
    }});
    document.getElementById('refresh-balances-button').addEventListener('click', refreshBalances);
    document.getElementById('pay-balance-button').addEventListener('click', async () => {{
      const accountId = document.getElementById('booking-account-id').value.trim();
      const chainCode = document.querySelector('#booking-form [name="payment_chain_code"]').value;
      const tokenSymbol = document.querySelector('#booking-form [name="payment_token_symbol"]').value;
      if (!lastPaymentId) {{
        out('booking-output', 'Create a booking first, then pay it from balance.');
        return;
      }}
      if (!accountId) {{
        out('booking-output', 'Account ID is required to pay from balance.');
        return;
      }}
      try {{
        const result = await request(`/payments/${{lastPaymentId}}/pay-from-balance`, {{
          method: 'POST',
          headers: {{'content-type': 'application/json'}},
          body: JSON.stringify({{ account_id: accountId, chain_code: chainCode, token_symbol: tokenSymbol }})
        }});
        out('booking-output', result);
        await refreshBalances();
        await loadData();
      }} catch (error) {{ out('booking-output', error); }}
    }});
    loadData().catch(error => out('booking-output', error));
  </script>
</body>
</html>"""


@api.get("/summary")
def summary_api() -> dict:
    return marketplace().summary().model_dump(mode="json")


@api.get("/pool-cards")
def pool_cards_api(
    hardware_type: HardwareType | None = None,
    active_only: bool = True,
    limit: int = 100,
) -> list[dict]:
    return [
        card.model_dump(mode="json")
        for card in marketplace().pool_cards(
            hardware_type=hardware_type,
            active_only=active_only,
            limit=limit,
        )
    ]


@api.post("/pools")
def create_pool_api(payload: MiningPoolCreateRequest) -> dict:
    try:
        return marketplace().create_pool(payload).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/pools")
def pools_api(
    hardware_type: HardwareType | None = None,
    paired_coin: str | None = None,
    active_only: bool = True,
    limit: int = 100,
) -> list[dict]:
    return [
        pool.model_dump(mode="json")
        for pool in marketplace().list_pools(
            hardware_type=hardware_type,
            paired_coin=paired_coin,
            active_only=active_only,
            limit=limit,
        )
    ]


@api.get("/pools/{pool_id}")
def pool_api(pool_id: str) -> dict:
    try:
        return marketplace().get_pool(pool_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/listings")
def create_listing_api(payload: ListingCreateRequest) -> dict:
    try:
        return marketplace().create_listing(payload).model_dump(mode="json")
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/listings")
def listings_api(
    pool_id: str | None = None,
    hardware_type: HardwareType | None = None,
    active_only: bool = True,
    limit: int = 100,
) -> list[dict]:
    return [
        listing.model_dump(mode="json")
        for listing in marketplace().list_listings(
            pool_id=pool_id,
            hardware_type=hardware_type,
            active_only=active_only,
            limit=limit,
        )
    ]


@api.get("/listings/{listing_id}")
def listing_api(listing_id: str) -> dict:
    try:
        return marketplace().get_listing(listing_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/workers/register")
def register_worker_api(payload: WorkerRegisterRequest) -> dict:
    try:
        worker, listing = marketplace().register_worker(payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "worker": worker.model_dump(mode="json"),
        "listing": listing.model_dump(mode="json"),
    }


@api.post("/workers/{worker_id}/heartbeat")
def heartbeat_worker_api(worker_id: str, payload: WorkerHeartbeatRequest) -> dict:
    try:
        worker, listing = marketplace().heartbeat_worker(worker_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "worker": worker.model_dump(mode="json"),
        "listing": listing.model_dump(mode="json"),
    }


@api.get("/workers")
def workers_api(
    provider_id: str | None = None,
    pool_id: str | None = None,
    status: WorkerStatus | None = None,
    limit: int = 100,
) -> list[dict]:
    return [
        worker.model_dump(mode="json")
        for worker in marketplace().list_workers(
            provider_id=provider_id,
            pool_id=pool_id,
            status=status,
            limit=limit,
        )
    ]


@api.get("/workers/{worker_id}/assignments")
def worker_assignments_api(worker_id: str, active_only: bool = True, limit: int = 100) -> list[dict]:
    try:
        return [
            assignment.model_dump(mode="json")
            for assignment in marketplace().worker_assignments(
                worker_id,
                active_only=active_only,
                limit=limit,
            )
        ]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/workers/{worker_id}/assignments/{booking_id}/reports")
def report_assignment_api(worker_id: str, booking_id: str, payload: AssignmentReportRequest) -> dict:
    try:
        return marketplace().report_assignment(worker_id, booking_id, payload).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/assignment-reports")
def assignment_reports_api(
    worker_id: str | None = None,
    booking_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    return [
        report.model_dump(mode="json")
        for report in marketplace().list_assignment_reports(
            worker_id=worker_id,
            booking_id=booking_id,
            limit=limit,
        )
    ]


@api.post("/workers/maintenance/expire-stale")
def expire_stale_workers_api(stale_after_seconds: int = 120, limit: int = 1000) -> dict:
    return marketplace().expire_stale_workers(
        stale_after_seconds=stale_after_seconds,
        limit=limit,
    )


@api.get("/workers/{worker_id}")
def worker_api(worker_id: str) -> dict:
    try:
        return marketplace().get_worker(worker_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/bookings")
def create_booking_api(payload: BookingCreateRequest) -> dict:
    try:
        booking, payment = marketplace().create_booking(payload)
    except (KeyError, LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "booking": booking.model_dump(mode="json"),
        "payment": payment.model_dump(mode="json"),
    }


@api.post("/bookings/quote")
def quote_booking_api(payload: BookingQuoteRequest) -> dict:
    try:
        return marketplace().quote_booking(payload).model_dump(mode="json")
    except (KeyError, LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/bookings")
def bookings_api(requester_wallet: str | None = None, limit: int = 100) -> list[dict]:
    return [
        booking.model_dump(mode="json")
        for booking in marketplace().list_bookings(requester_wallet=requester_wallet, limit=limit)
    ]


@api.get("/bookings/{booking_id}")
def booking_api(booking_id: str) -> dict:
    try:
        return marketplace().get_booking(booking_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.get("/payments/{payment_id}")
def payment_api(payment_id: str) -> dict:
    try:
        return marketplace().get_payment(payment_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/payments/{payment_id}/submit")
def submit_payment_api(payment_id: str, payload: PaymentSubmitRequest) -> dict:
    try:
        booking, payment = marketplace().submit_payment(payment_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "booking": booking.model_dump(mode="json"),
        "payment": payment.model_dump(mode="json"),
    }


@api.post("/payments/{payment_id}/pay-from-balance")
def pay_from_balance_api(payment_id: str, payload: PayFromBalanceRequest) -> dict:
    try:
        booking, payment, ledger_entry = marketplace().pay_payment_from_balance(payment_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "booking": booking.model_dump(mode="json"),
        "payment": payment.model_dump(mode="json"),
        "ledger_entry": ledger_entry.model_dump(mode="json"),
    }


@api.post("/bookings/{booking_id}/release")
def release_booking_api(booking_id: str) -> dict:
    try:
        return marketplace().release_booking(booking_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/settlements/bookings/{booking_id}")
def settle_booking_api(booking_id: str) -> dict:
    try:
        return marketplace().settle_booking(booking_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/settlements")
def settlements_api(
    provider_id: str | None = None,
    status: SettlementStatus | None = None,
    limit: int = 100,
) -> list[dict]:
    return [
        settlement.model_dump(mode="json")
        for settlement in marketplace().list_settlements(
            provider_id=provider_id,
            status=status,
            limit=limit,
        )
    ]


@api.get("/settlements/{settlement_id}")
def settlement_api(settlement_id: str) -> dict:
    try:
        return marketplace().get_settlement(settlement_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def main() -> None:
    uvicorn.run(
        "picoin_marketplace.api:api",
        host=os.getenv("PICOIN_MARKETPLACE_HOST", "127.0.0.1"),
        port=int(os.getenv("PICOIN_MARKETPLACE_PORT", "9410")),
        reload=os.getenv("PICOIN_MARKETPLACE_RELOAD", "").lower() in {"1", "true", "yes"},
    )


if __name__ == "__main__":
    main()
