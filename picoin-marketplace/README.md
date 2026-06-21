# Picoin Marketplace

Standalone marketplace app for CPU, GPU, and ASIC mining capacity at:

```text
marketplace.picoin.science
```

This is not a Layer 2 protocol module. It is an application marketplace where
providers publish mining capacity into paired pools and customers reserve access.
Payments can be made with Picoin or with Ethereum tokens explicitly enabled by
the marketplace operator.

## Core Rule

```text
pricing_currency = PICO
accepted_payment_rails = PICO + approved EVM tokens
```

The marketplace prices capacity in PICO. A customer can fund an account with
PICO or an approved Ethereum token such as ETH/ERC20, and the marketplace
converts the booking invoice through the token's configured `pico_rate`.

The paired coins are not payment currencies. They define how the rented mining
capacity is split inside each pool.

## Pool Model

Each pool is defined by:

- one hardware type: `cpu`, `gpu`, or `asic`
- one paired mining target: for example `MONERO`, `DOGE`, `RAVENCOIN`, or `LITECOIN`
- one pair symbol: `PICO/MONERO`, `PICO/DOGE`, `PICO/RAVENCOIN`, `PICO/LITECOIN`
- one capacity split: default `10%` Picoin and `90%` paired coin
- payment rails: Picoin native plus approved Ethereum tokens

Example pairs:

```text
CPU  PICO/MONERO
GPU  PICO/RAVENCOIN
ASIC PICO/DOGE
ASIC PICO/LITECOIN
```

These four pools are seeded by default on startup. Set
`PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS=0` to disable default pool creation.

The default policy means that when a customer reserves 10 GPU units from a
`PICO/RAVENCOIN` pool, the booking records:

```text
1.0 GPU unit  -> Picoin support capacity
9.0 GPU units -> Ravencoin target capacity
```

The customer still buys marketplace capacity priced in Picoin. If an Ethereum
token is used, it is converted by the marketplace using the configured token
rate.

## MVP Flow

1. An operator creates a paired pool such as `PICO/MONERO`.
2. A provider publishes CPU, GPU, or ASIC capacity into that pool.
3. A customer creates a booking request for a pool or hardware type.
4. The marketplace selects capacity and creates a payment order.
5. The customer deposits PICO or an approved Ethereum token into the marketplace
   deposit address.
6. The scanner records the deposit, waits for confirmations, and credits the
   account ledger once.
7. The customer pays the booking from the confirmed internal balance.
8. When the rental ends, the booking is released and capacity returns to the listing.

## Run Locally

```bash
cd picoin-marketplace
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
picoin-marketplace
```

Windows PowerShell:

```powershell
cd picoin-marketplace
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
picoin-marketplace
```

Open:

```text
http://127.0.0.1:9410/
```

The root page is an Easy Mining style dashboard with pool cards, hardware
filters, quick order, capacity publishing, pair pool creation, account
registration, wallet verification, deposit intake, balance display, and
pay-from-balance checkout.

## UI Checkout Flow

The dashboard supports a complete local operator flow:

1. Create an account in `Accounts & Deposits`.
2. Register and verify a Picoin or Ethereum wallet for that account.
3. Record a scanner deposit and process confirmations.
4. Select a pool in `Quick Order`.
5. Create the capacity reservation.
6. Click `Pay from confirmed balance`.

After payment, the booking changes to `active` and the account balance is
debited through the internal ledger.

## Environment

```bash
PICOIN_MARKETPLACE_STATE_DIR=.picoin-marketplace-state
PICOIN_MARKETPLACE_ESCROW_ADDRESS=PI_MARKETPLACE_ESCROW
PICOIN_MARKETPLACE_EVM_ESCROW_ADDRESS=0x0000000000000000000000000000000000000000
PICOIN_MARKETPLACE_CONFIRMATIONS_REQUIRED=1
PICOIN_MARKETPLACE_ETH_CONFIRMATIONS=12
PICOIN_MARKETPLACE_ETH_PICO_RATE=1000
PICOIN_MARKETPLACE_ETH_RPC_URLS=
PICOIN_MARKETPLACE_PAYMENT_WINDOW_MINUTES=30
PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS=1
PICOIN_MARKETPLACE_HOST=127.0.0.1
PICOIN_MARKETPLACE_PORT=9410
```

## API

```text
GET  /health
GET  /
POST /accounts
GET  /accounts
GET  /accounts/{account_id}
POST /accounts/{account_id}/wallets
GET  /wallets
POST /wallets/{wallet_id}/verify
POST /chains
GET  /chains
POST /tokens
GET  /tokens
GET  /scanner/{chain_code}/config
POST /scanner/deposits
POST /scanner/{chain_code}/confirmations/process
POST /scanner/picoin/import-history
POST /scanner/picoin/poll
GET  /deposits
GET  /ledger
GET  /accounts/{account_id}/balances
GET  /summary
GET  /pool-cards
POST /pools
GET  /pools
GET  /pools/{pool_id}
POST /listings
GET  /listings
GET  /listings/{listing_id}
POST /bookings/quote
POST /bookings
GET  /bookings
GET  /bookings/{booking_id}
GET  /payments/{payment_id}
POST /payments/{payment_id}/submit
POST /payments/{payment_id}/pay-from-balance
POST /bookings/{booking_id}/release
```

## Accounts, Deposits, And Ledger

The marketplace includes a Nodezero-style deposit flow:

1. Create an account.
2. Register a Picoin or Ethereum wallet.
3. Verify the wallet.
4. Scanner reports a deposit from the verified wallet to the marketplace deposit
   address.
5. Confirmation processor credits the ledger once the chain requirement is met.
6. A booking payment can be paid from that confirmed ledger balance.

Deposits are idempotent by:

```text
chain_code + tx_hash + log_index
```

This prevents double-crediting if the scanner submits the same event more than
once.

### Create Account

```bash
curl -sS -X POST http://127.0.0.1:9410/accounts \
  -H 'content-type: application/json' \
  -d '{
    "email": "customer@example.com",
    "display_name": "Customer"
  }' | python -m json.tool
```

### Register And Verify Wallet

```bash
curl -sS -X POST http://127.0.0.1:9410/accounts/ACCOUNT_ID/wallets \
  -H 'content-type: application/json' \
  -d '{
    "chain_code": "picoin",
    "address": "PI_CUSTOMER_WALLET"
  }' | python -m json.tool

curl -sS -X POST http://127.0.0.1:9410/wallets/WALLET_ID/verify \
  | python -m json.tool
```

Ethereum wallets use normal EVM addresses:

```text
0x1111111111111111111111111111111111111111
```

### Scanner Deposit

Picoin deposit:

```bash
curl -sS -X POST http://127.0.0.1:9410/scanner/deposits \
  -H 'content-type: application/json' \
  -d '{
    "chain_code": "picoin",
    "token_symbol": "PICO",
    "from_address": "PI_CUSTOMER_WALLET",
    "to_address": "PI_MARKETPLACE_ESCROW",
    "amount_base_units": "10000000",
    "tx_hash": "abcdef1234567890abcdef1234567890",
    "block_number": 100
  }' | python -m json.tool
```

Process confirmations:

```bash
curl -sS -X POST http://127.0.0.1:9410/scanner/picoin/confirmations/process \
  -H 'content-type: application/json' \
  -d '{"latest_block_number": 102}' | python -m json.tool
```

### Poll A Picoin Node

The marketplace can poll a Picoin node history endpoint directly:

```bash
curl -sS -X POST http://127.0.0.1:9410/scanner/picoin/poll \
  -H 'content-type: application/json' \
  -d '{
    "node_url": "http://127.0.0.1:8000",
    "limit": 50,
    "confirmed_only": true
  }' | python -m json.tool
```

The scanner calls:

```text
/transactions/history?address=PI_MARKETPLACE_ESCROW&limit=50&confirmed_only=true&backfill=true
```

Then it imports only valid confirmed incoming deposits:

```text
recipient = marketplace Picoin escrow address
block_height > 0
status = confirmed
sender = a verified account wallet
tx_hash = present
```

Repeated rows are safe because deposits are idempotent by
`chain_code + tx_hash + log_index`.

Check balances:

```bash
curl -sS http://127.0.0.1:9410/accounts/ACCOUNT_ID/balances \
  | python -m json.tool
```

### Pay A Booking From Balance

```bash
curl -sS -X POST http://127.0.0.1:9410/payments/PAYMENT_ID/pay-from-balance \
  -H 'content-type: application/json' \
  -d '{
    "account_id": "ACCOUNT_ID",
    "chain_code": "picoin",
    "token_symbol": "PICO"
  }' | python -m json.tool
```

The response activates the booking when the account has enough confirmed
balance.

## Example Pool

```bash
curl -sS -X POST http://127.0.0.1:9410/pools \
  -H 'content-type: application/json' \
  -d '{
    "hardware_type": "gpu",
    "paired_coin": "ravencoin",
    "name": "GPU PICO/RAVENCOIN pool",
    "picoin_capacity_percent": 10,
    "paired_capacity_percent": 90
  }' | python -m json.tool
```

The response contains the `pool_id` used by listings and bookings.

## Easy Mining Pool Cards

The UI uses `/pool-cards` to present each pair as a product card instead of a
raw database row.

```bash
curl -sS "http://127.0.0.1:9410/pool-cards?active_only=true" \
  | python -m json.tool
```

Each card includes:

```text
pair_symbol
hardware_type
algorithm
available_units
min_price_pi_per_hour
picoin_capacity_percent
paired_capacity_percent
can_book
```

This is the marketplace-facing layer for a NiceHash EasyMining-like flow:
choose the pair, choose units and duration, then reserve and pay in PICO.

## Quote Before Booking

Use `/bookings/quote` to preview the exact Picoin cost and capacity split before
creating a booking.

```bash
curl -sS -X POST http://127.0.0.1:9410/bookings/quote \
  -H 'content-type: application/json' \
  -d '{
    "pool_id": "pool_FROM_THE_POOL_RESPONSE",
    "units": 5,
    "duration_minutes": 360
  }' | python -m json.tool
```

The UI exposes this as quick packages:

```text
Starter  1 unit  / 1 hour
Boost    5 units / 6 hours
Pro      10 units / 24 hours
```

The quote does not reserve capacity. Capacity is reserved only when `/bookings`
creates the actual booking and payment order.

## Example GPU Listing In A Pool

```bash
curl -sS -X POST http://127.0.0.1:9410/listings \
  -H 'content-type: application/json' \
  -d '{
    "pool_id": "pool_FROM_THE_POOL_RESPONSE",
    "provider_id": "provider-gpu-1",
    "provider_wallet": "PI_PROVIDER_WALLET",
    "hardware_type": "gpu",
    "title": "RTX 4090 GPU node",
    "units_total": 2,
    "price_pi_per_hour": 3.1416,
    "region": "nyc",
    "capabilities": ["llm", "inference", "cuda"],
    "gpu_model": "RTX 4090",
    "gpu_count": 2,
    "gpu_vram_gb": 24
  }' | python -m json.tool
```

## Example Booking

```bash
curl -sS -X POST http://127.0.0.1:9410/bookings \
  -H 'content-type: application/json' \
  -d '{
    "account_id": "ACCOUNT_ID",
    "requester_wallet": "PI_CUSTOMER_WALLET",
    "pool_id": "pool_FROM_THE_POOL_RESPONSE",
    "units": 1,
    "duration_minutes": 60,
    "payment_chain_code": "picoin",
    "payment_token_symbol": "PICO",
    "required_capabilities": ["llm", "cuda"]
  }' | python -m json.tool
```

The response contains:

```text
booking.status = awaiting_payment
booking.pair_symbol = PICO/RAVENCOIN
booking.picoin_capacity_percent = 10
booking.paired_capacity_percent = 90
payment.currency = PICO
payment.amount_pi = exact Picoin amount
payment.pay_to_address = marketplace escrow address
payment.memo = booking id
```

## Submit External Payment Hash

```bash
curl -sS -X POST http://127.0.0.1:9410/payments/PAYMENT_ID/submit \
  -H 'content-type: application/json' \
  -d '{
    "tx_hash": "PICOIN_TRANSACTION_HASH",
    "confirmations": 1
  }' | python -m json.tool
```

When confirmations are enough:

```text
payment.status = confirmed
booking.status = active
```

This endpoint is still available for manual confirmation flows. The preferred
account flow is to credit a wallet deposit through `/scanner/deposits` and then
use `/payments/{payment_id}/pay-from-balance`.

## Notes

- The MVP stores state in SQLite.
- Deposits and ledger credits are idempotent so repeated scanner events cannot
  double-credit the same transaction.
- The paired mining coin is operational metadata for capacity routing; it is
  not accepted as payment.
- Marketplace fees, escrow settlement, provider ratings, dispute handling, and
  automatic node-side payment verification are next phases.
