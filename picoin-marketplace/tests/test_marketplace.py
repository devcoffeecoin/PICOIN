from __future__ import annotations

from fastapi.testclient import TestClient

from picoin_marketplace import api as marketplace_api


def evm_topic(address: str) -> str:
    return "0x" + ("0" * 24) + address.lower().removeprefix("0x")


def test_gpu_listing_booking_payment_and_release(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_ESCROW_ADDRESS", "PI_ESCROW_MARKET")
    monkeypatch.setenv("PICOIN_MARKETPLACE_CONFIRMATIONS_REQUIRED", "2")
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    client = TestClient(marketplace_api.api)

    pool_response = client.post(
        "/pools",
        json={
            "hardware_type": "gpu",
            "paired_coin": "monero",
            "name": "GPU PICO/MONERO pool",
        },
    )
    assert pool_response.status_code == 200
    pool = pool_response.json()
    assert pool["pair_symbol"] == "PICO/MONERO"
    assert pool["picoin_capacity_percent"] == 10.0
    assert pool["paired_capacity_percent"] == 90.0

    listing_response = client.post(
        "/listings",
        json={
            "pool_id": pool["pool_id"],
            "provider_id": "provider-gpu-1",
            "provider_wallet": "PI_PROVIDER_WALLET",
            "hardware_type": "gpu",
            "title": "RTX 4090 GPU node",
            "units_total": 2,
            "price_pi_per_hour": 3.1416,
            "region": "nyc",
            "capabilities": ["llm", "cuda"],
            "gpu_model": "RTX 4090",
            "gpu_count": 2,
            "gpu_vram_gb": 24,
        },
    )
    assert listing_response.status_code == 200
    listing = listing_response.json()
    assert listing["currency"] == "PICO"
    assert listing["hardware_type"] == "gpu"
    assert listing["pool_id"] == pool["pool_id"]
    assert listing["pair_symbol"] == "PICO/MONERO"
    assert listing["units_available"] == 2

    booking_response = client.post(
        "/bookings",
        json={
            "requester_wallet": "PI_CUSTOMER_WALLET",
            "pool_id": pool["pool_id"],
            "units": 1,
            "duration_minutes": 60,
            "required_capabilities": ["cuda"],
        },
    )
    assert booking_response.status_code == 200
    payload = booking_response.json()
    booking = payload["booking"]
    payment = payload["payment"]
    assert booking["status"] == "awaiting_payment"
    assert booking["currency"] == "PICO"
    assert booking["pair_symbol"] == "PICO/MONERO"
    assert booking["picoin_capacity_units"] == 0.1
    assert booking["paired_capacity_units"] == 0.9
    assert booking["amount_pi"] == 3.1416
    assert payment["currency"] == "PICO"
    assert payment["amount_pi"] == 3.1416
    assert payment["pay_to_address"] == "PI_ESCROW_MARKET"
    assert payment["memo"] == booking["booking_id"]

    updated_listing = client.get(f"/listings/{listing['listing_id']}").json()
    assert updated_listing["units_available"] == 1

    submitted = client.post(
        f"/payments/{payment['payment_id']}/submit",
        json={"tx_hash": "abcdef1234567890abcdef", "confirmations": 1},
    ).json()
    assert submitted["payment"]["status"] == "submitted"
    assert submitted["booking"]["status"] == "awaiting_payment"

    confirmed = client.post(
        f"/payments/{payment['payment_id']}/submit",
        json={"tx_hash": "abcdef1234567890abcdef", "confirmations": 2},
    ).json()
    assert confirmed["payment"]["status"] == "confirmed"
    assert confirmed["booking"]["status"] == "active"

    released = client.post(f"/bookings/{booking['booking_id']}/release").json()
    assert released["status"] == "released"
    restored_listing = client.get(f"/listings/{listing['listing_id']}").json()
    assert restored_listing["units_available"] == 2


def test_booking_requires_matching_capacity(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    client = TestClient(marketplace_api.api)

    response = client.post(
        "/bookings",
        json={
            "requester_wallet": "PI_CUSTOMER_WALLET",
            "hardware_type": "asic",
            "units": 1,
            "duration_minutes": 60,
        },
    )

    assert response.status_code == 400
    assert "no matching capacity listing available" in response.json()["detail"]


def test_summary_counts_cpu_gpu_and_asic(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    client = TestClient(marketplace_api.api)

    for hardware_type, paired_coin in [("cpu", "doge"), ("gpu", "ravencoin"), ("asic", "litecoin")]:
        pool = client.post(
            "/pools",
            json={
                "hardware_type": hardware_type,
                "paired_coin": paired_coin,
            },
        ).json()
        response = client.post(
            "/listings",
            json={
                "pool_id": pool["pool_id"],
                "provider_id": f"provider-{hardware_type}",
                "provider_wallet": f"PI_PROVIDER_{hardware_type.upper()}",
                "hardware_type": hardware_type,
                "title": f"{hardware_type.upper()} node",
                "units_total": 3,
                "price_pi_per_hour": 1.0,
            },
        )
        assert response.status_code == 200

    summary = client.get("/summary").json()

    assert summary["currency"] == "PICO"
    assert summary["active_pool_count"] == 3
    assert summary["active_listing_count"] == 3
    assert summary["active_pairs"] == ["PICO/DOGE", "PICO/LITECOIN", "PICO/RAVENCOIN"]
    assert summary["total_units_by_hardware"] == {"cpu": 3, "gpu": 3, "asic": 3}
    assert summary["available_units_by_hardware"] == {"cpu": 3, "gpu": 3, "asic": 3}


def test_pool_split_must_total_100(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    client = TestClient(marketplace_api.api)

    response = client.post(
        "/pools",
        json={
            "hardware_type": "cpu",
            "paired_coin": "doge",
            "picoin_capacity_percent": 20,
            "paired_capacity_percent": 90,
        },
    )

    assert response.status_code == 400
    assert "split must total 100" in response.json()["detail"]


def test_default_pools_are_seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "1")
    client = TestClient(marketplace_api.api)

    response = client.get("/pools")

    assert response.status_code == 200
    pairs = sorted((pool["hardware_type"], pool["pair_symbol"]) for pool in response.json())
    assert pairs == [
        ("asic", "PICO/DOGE"),
        ("asic", "PICO/LITECOIN"),
        ("cpu", "PICO/MONERO"),
        ("gpu", "PICO/RAVENCOIN"),
    ]


def test_home_returns_operator_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "1")
    client = TestClient(marketplace_api.api)

    response = client.get("/")

    assert response.status_code == 200
    assert "Picoin Marketplace" in response.text
    assert "Easy Mining Pools" in response.text
    assert "Quick Order" in response.text
    assert "Create Pair Pool" in response.text
    assert "Publish Capacity" in response.text
    assert "Worker Agents" in response.text
    assert "Register worker" in response.text
    assert "Pay from confirmed balance" in response.text
    assert "Accounts & Deposits" in response.text


def test_pool_cards_show_availability_and_price(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    client = TestClient(marketplace_api.api)

    pool = client.post(
        "/pools",
        json={
            "hardware_type": "gpu",
            "paired_coin": "ravencoin",
        },
    ).json()

    empty_cards = client.get("/pool-cards").json()
    assert empty_cards[0]["pair_symbol"] == "PICO/RAVENCOIN"
    assert empty_cards[0]["available_units"] == 0
    assert empty_cards[0]["can_book"] is False
    assert empty_cards[0]["status"] == "waiting_capacity"

    client.post(
        "/listings",
        json={
            "pool_id": pool["pool_id"],
            "provider_id": "provider-gpu-1",
            "provider_wallet": "PI_PROVIDER_GPU",
            "hardware_type": "gpu",
            "title": "Ravencoin GPU rig",
            "units_total": 5,
            "price_pi_per_hour": 1.25,
        },
    )

    cards = client.get("/pool-cards").json()
    card = cards[0]
    assert card["available_units"] == 5
    assert card["active_listing_count"] == 1
    assert card["min_price_pi_per_hour"] == 1.25
    assert card["estimated_one_hour_pi"] == 1.25
    assert card["can_book"] is True
    assert card["status"] == "available"


def test_worker_registration_and_heartbeat_manage_capacity(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    client = TestClient(marketplace_api.api)

    pool = client.post(
        "/pools",
        json={
            "hardware_type": "gpu",
            "paired_coin": "ravencoin",
        },
    ).json()

    registered = client.post(
        "/workers/register",
        json={
            "worker_id": "worker-gpu-1",
            "provider_id": "provider-gpu-1",
            "provider_wallet": "PI_PROVIDER_GPU",
            "pool_id": pool["pool_id"],
            "hardware_type": "gpu",
            "title": "GPU worker node",
            "units_total": 3,
            "price_pi_per_hour": 2.0,
            "gpu_model": "RTX 4090",
            "gpu_count": 3,
            "gpu_vram_gb": 24,
            "agent_version": "0.1.0",
        },
    )

    assert registered.status_code == 200
    worker_payload = registered.json()
    worker = worker_payload["worker"]
    listing = worker_payload["listing"]
    assert worker["worker_id"] == "worker-gpu-1"
    assert worker["listing_id"] == listing["listing_id"]
    assert listing["units_total"] == 3
    assert listing["units_available"] == 3

    booking_payload = client.post(
        "/bookings",
        json={
            "requester_wallet": "PI_CUSTOMER_WALLET",
            "pool_id": pool["pool_id"],
            "units": 2,
            "duration_minutes": 60,
        },
    ).json()
    booking = booking_payload["booking"]
    payment = booking_payload["payment"]
    client.post(
        f"/payments/{payment['payment_id']}/submit",
        json={"tx_hash": "abcdef1234567890abcdef", "confirmations": 1},
    )

    heartbeat = client.post(
        "/workers/worker-gpu-1/heartbeat",
        json={
            "status": "online",
            "units_total": 3,
            "units_available": 3,
            "metrics": {"temperature_c": 65},
        },
    ).json()
    assert heartbeat["worker"]["metrics"] == {"temperature_c": 65}
    assert heartbeat["listing"]["units_available"] == 1
    assert heartbeat["listing"]["status"] == "active"

    report = client.post(
        f"/workers/worker-gpu-1/assignments/{booking['booking_id']}/reports",
        json={
            "status": "running",
            "reported_hashrate": 125.5,
            "accepted_shares": 42,
            "rejected_shares": 1,
            "uptime_seconds": 600,
            "message": "running kawpow split",
            "metrics": {"temperature_c": 64},
        },
    ).json()
    assert report["worker_id"] == "worker-gpu-1"
    assert report["booking_id"] == booking["booking_id"]
    assert report["status"] == "running"
    assert report["reported_hashrate"] == 125.5
    assert report["accepted_shares"] == 42
    assert report["pair_symbol"] == "PICO/RAVENCOIN"
    assert 0 <= report["progress_percent"] <= 100

    paused = client.post(
        "/workers/worker-gpu-1/heartbeat",
        json={"status": "paused", "units_total": 3, "units_available": 3},
    ).json()
    assert paused["worker"]["status"] == "paused"
    assert paused["listing"]["status"] == "paused"
    assert paused["listing"]["units_available"] == 0

    listed_workers = client.get("/workers?provider_id=provider-gpu-1").json()
    assert len(listed_workers) == 1
    assert listed_workers[0]["worker_id"] == "worker-gpu-1"

    assignments = client.get("/workers/worker-gpu-1/assignments").json()
    assert len(assignments) == 1
    assert assignments[0]["booking_id"] == booking["booking_id"]
    assert assignments[0]["status"] == "active"
    assert assignments[0]["pair_symbol"] == "PICO/RAVENCOIN"
    assert assignments[0]["picoin_capacity_units"] == 0.2
    assert assignments[0]["paired_capacity_units"] == 1.8
    assert assignments[0]["latest_report"]["report_id"] == report["report_id"]
    assert assignments[0]["latest_report"]["accepted_shares"] == 42

    reports = client.get(f"/assignment-reports?worker_id=worker-gpu-1&booking_id={booking['booking_id']}").json()
    assert len(reports) == 1
    assert reports[0]["report_id"] == report["report_id"]

    settlement = client.post(f"/settlements/bookings/{booking['booking_id']}").json()
    assert settlement["status"] == "accrued"
    assert settlement["provider_id"] == "provider-gpu-1"
    assert settlement["provider_wallet"] == "PI_PROVIDER_GPU"
    assert settlement["gross_amount_base_units"] == "4000000"
    assert settlement["fee_amount_base_units"] == "40000"
    assert settlement["provider_amount_base_units"] == "3960000"
    assert settlement["currency"] == "PICO"

    duplicate_settlement = client.post(f"/settlements/bookings/{booking['booking_id']}").json()
    assert duplicate_settlement["settlement_id"] == settlement["settlement_id"]
    provider_settlements = client.get("/settlements?provider_id=provider-gpu-1").json()
    assert len(provider_settlements) == 1

    client.post(f"/bookings/{booking['booking_id']}/release")
    online = client.post(
        "/workers/worker-gpu-1/heartbeat",
        json={"status": "online", "units_total": 3, "units_available": 3},
    ).json()
    assert online["listing"]["status"] == "active"
    assert online["listing"]["units_available"] == 3

    expired = client.post("/workers/maintenance/expire-stale?stale_after_seconds=0").json()
    assert expired["expired"] == 1
    worker_after_expiry = client.get("/workers/worker-gpu-1").json()
    listing_after_expiry = client.get(f"/listings/{listing['listing_id']}").json()
    assert worker_after_expiry["status"] == "offline"
    assert listing_after_expiry["status"] == "paused"
    assert listing_after_expiry["units_available"] == 0


def test_booking_quote_does_not_reserve_capacity(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    client = TestClient(marketplace_api.api)

    pool = client.post(
        "/pools",
        json={
            "hardware_type": "cpu",
            "paired_coin": "monero",
        },
    ).json()
    listing = client.post(
        "/listings",
        json={
            "pool_id": pool["pool_id"],
            "provider_id": "provider-cpu-1",
            "provider_wallet": "PI_PROVIDER_CPU",
            "hardware_type": "cpu",
            "title": "RandomX CPU rig",
            "units_total": 10,
            "price_pi_per_hour": 0.5,
        },
    ).json()

    quote_response = client.post(
        "/bookings/quote",
        json={
            "pool_id": pool["pool_id"],
            "units": 5,
            "duration_minutes": 360,
        },
    )

    assert quote_response.status_code == 200
    quote = quote_response.json()
    assert quote["amount_pi"] == 15.0
    assert quote["picoin_capacity_units"] == 0.5
    assert quote["paired_capacity_units"] == 4.5
    assert quote["available_units_after_quote"] == 5
    assert quote["can_book"] is True

    unchanged_listing = client.get(f"/listings/{listing['listing_id']}").json()
    assert unchanged_listing["units_available"] == 10


def test_account_picoin_deposit_confirmation_and_balance_payment(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_CONFIRMATIONS_REQUIRED", "2")
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    client = TestClient(marketplace_api.api)

    account = client.post(
        "/accounts",
        json={"email": "customer@example.com", "display_name": "Customer"},
    ).json()
    wallet = client.post(
        f"/accounts/{account['account_id']}/wallets",
        json={"chain_code": "picoin", "address": "PI_CUSTOMER_WALLET"},
    ).json()
    verified = client.post(f"/wallets/{wallet['wallet_id']}/verify").json()
    assert verified["status"] == "verified"

    deposit_payload = {
        "chain_code": "picoin",
        "token_symbol": "PICO",
        "from_address": "PI_CUSTOMER_WALLET",
        "to_address": "PI_MARKETPLACE_ESCROW",
        "amount_base_units": "10000000",
        "tx_hash": "abcdef1234567890abcdef1234567890",
        "block_number": 100,
    }
    deposit = client.post("/scanner/deposits", json=deposit_payload).json()
    duplicate = client.post("/scanner/deposits", json=deposit_payload).json()
    assert duplicate["deposit_id"] == deposit["deposit_id"]

    processed = client.post(
        "/scanner/picoin/confirmations/process",
        json={"latest_block_number": 101},
    ).json()
    assert processed["processed"] == 1
    assert processed["credited"] == 1

    balances = client.get(f"/accounts/{account['account_id']}/balances").json()
    pico_balance = next(row for row in balances if row["token_symbol"] == "PICO")
    assert pico_balance["available_base_units"] == "10000000"
    assert pico_balance["available"] == "10"

    pool = client.post(
        "/pools",
        json={"hardware_type": "gpu", "paired_coin": "ravencoin"},
    ).json()
    client.post(
        "/listings",
        json={
            "pool_id": pool["pool_id"],
            "provider_id": "provider-gpu-1",
            "provider_wallet": "PI_PROVIDER_GPU",
            "hardware_type": "gpu",
            "title": "Ravencoin GPU rig",
            "units_total": 1,
            "price_pi_per_hour": 3.1416,
        },
    )
    booking_payload = client.post(
        "/bookings",
        json={
            "account_id": account["account_id"],
            "requester_wallet": "PI_CUSTOMER_WALLET",
            "pool_id": pool["pool_id"],
            "units": 1,
            "duration_minutes": 60,
        },
    ).json()
    payment = booking_payload["payment"]
    assert payment["amount_base_units"] == "3141600"

    paid = client.post(
        f"/payments/{payment['payment_id']}/pay-from-balance",
        json={"account_id": account["account_id"], "chain_code": "picoin", "token_symbol": "PICO"},
    ).json()
    assert paid["booking"]["status"] == "active"
    assert paid["payment"]["status"] == "confirmed"
    assert paid["ledger_entry"]["direction"] == "debit"

    balances_after = client.get(f"/accounts/{account['account_id']}/balances").json()
    pico_after = next(row for row in balances_after if row["token_symbol"] == "PICO")
    assert pico_after["available_base_units"] == "6858400"

def test_ethereum_token_deposit_can_pay_marketplace_booking(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    monkeypatch.setenv("PICOIN_MARKETPLACE_ETH_PICO_RATE", "1000")
    monkeypatch.setenv("PICOIN_MARKETPLACE_ETH_CONFIRMATIONS", "3")
    monkeypatch.setenv("PICOIN_MARKETPLACE_EVM_ESCROW_ADDRESS", "0x2222222222222222222222222222222222222222")
    client = TestClient(marketplace_api.api)

    account = client.post("/accounts", json={"email": "eth@example.com"}).json()
    wallet = client.post(
        f"/accounts/{account['account_id']}/wallets",
        json={
            "chain_code": "ethereum",
            "address": "0x1111111111111111111111111111111111111111",
        },
    ).json()
    client.post(f"/wallets/{wallet['wallet_id']}/verify")

    deposit = client.post(
        "/scanner/deposits",
        json={
            "chain_code": "ethereum",
            "token_symbol": "ETH",
            "from_address": "0x1111111111111111111111111111111111111111",
            "to_address": "0x2222222222222222222222222222222222222222",
            "amount_base_units": "10000000000000000",
            "tx_hash": "0x" + ("a" * 64),
            "block_number": 50,
            "log_index": 0,
        },
    )
    assert deposit.status_code == 200
    processed = client.post(
        "/scanner/ethereum/confirmations/process",
        json={"latest_block_number": 52},
    ).json()
    assert processed["credited"] == 1

    pool = client.post(
        "/pools",
        json={"hardware_type": "cpu", "paired_coin": "monero"},
    ).json()
    client.post(
        "/listings",
        json={
            "pool_id": pool["pool_id"],
            "provider_id": "provider-cpu-1",
            "provider_wallet": "PI_PROVIDER_CPU",
            "hardware_type": "cpu",
            "title": "RandomX CPU rig",
            "units_total": 1,
            "price_pi_per_hour": 3.1416,
        },
    )
    booking_payload = client.post(
        "/bookings",
        json={
            "account_id": account["account_id"],
            "requester_wallet": "PI_CUSTOMER_WALLET",
            "pool_id": pool["pool_id"],
            "units": 1,
            "duration_minutes": 60,
            "payment_chain_code": "ethereum",
            "payment_token_symbol": "ETH",
        },
    ).json()

    payment = booking_payload["payment"]
    assert payment["currency"] == "ETH"
    assert payment["amount_base_units"] == "3141600000000000"
    assert payment["pay_to_address"] == "0x2222222222222222222222222222222222222222"

    paid = client.post(
        f"/payments/{payment['payment_id']}/pay-from-balance",
        json={"account_id": account["account_id"], "chain_code": "ethereum", "token_symbol": "ETH"},
    ).json()
    assert paid["booking"]["status"] == "active"
    assert paid["payment"]["currency"] == "ETH"
    assert paid["ledger_entry"]["amount_base_units"] == "3141600000000000"


def test_picoin_history_import_scans_confirmed_deposits(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_CONFIRMATIONS_REQUIRED", "2")
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    client = TestClient(marketplace_api.api)

    account = client.post("/accounts", json={"email": "scan@example.com"}).json()
    wallet = client.post(
        f"/accounts/{account['account_id']}/wallets",
        json={"chain_code": "picoin", "address": "PI_SCAN_WALLET"},
    ).json()
    client.post(f"/wallets/{wallet['wallet_id']}/verify")

    history_row = {
        "tx_hash": "1234567890abcdef1234567890abcdef",
        "sender": "PI_SCAN_WALLET",
        "recipient": "PI_MARKETPLACE_ESCROW",
        "amount": "2.500000",
        "status": "confirmed",
        "block_height": 500,
    }
    payload = {
        "rows": [
            history_row,
            {**history_row, "tx_hash": "noheight", "block_height": 0},
            {**history_row, "recipient": "PI_OTHER_ESCROW", "tx_hash": "abcdef1234567890abcdef1234567890"},
        ],
        "latest_block_number": 501,
    }

    imported = client.post("/scanner/picoin/import-history", json=payload).json()

    assert imported["rows_seen"] == 3
    assert imported["imported"] == 1
    assert imported["skipped"] == 2
    assert imported["confirmation_result"]["credited"] == 1

    duplicate = client.post("/scanner/picoin/import-history", json=payload).json()
    assert duplicate["imported"] == 1
    assert duplicate["confirmation_result"]["credited"] == 0

    balances = client.get(f"/accounts/{account['account_id']}/balances").json()
    pico_balance = next(row for row in balances if row["token_symbol"] == "PICO")
    assert pico_balance["available_base_units"] == "2500000"


def test_picoin_node_poll_uses_history_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_CONFIRMATIONS_REQUIRED", "1")
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    client = TestClient(marketplace_api.api)

    account = client.post("/accounts", json={"email": "poll@example.com"}).json()
    wallet = client.post(
        f"/accounts/{account['account_id']}/wallets",
        json={"chain_code": "picoin", "address": "PI_POLL_WALLET"},
    ).json()
    client.post(f"/wallets/{wallet['wallet_id']}/verify")

    seen_urls = []

    def fake_fetch(url):
        seen_urls.append(url)
        return [
            {
                "tx_hash": "abcdefabcdefabcdefabcdefabcdef12",
                "sender": "PI_POLL_WALLET",
                "recipient": "PI_MARKETPLACE_ESCROW",
                "amount_units": 9000000,
                "status": "confirmed",
                "block_height": 700,
            }
        ]

    monkeypatch.setattr("picoin_marketplace.marketplace.fetch_json_url", fake_fetch)

    result = client.post(
        "/scanner/picoin/poll",
        json={"node_url": "http://node.local:8000", "limit": 25},
    ).json()

    assert result["imported"] == 1
    assert result["confirmation_result"]["credited"] == 1
    assert seen_urls
    assert "http://node.local:8000/transactions/history" in seen_urls[0]
    assert "address=PI_MARKETPLACE_ESCROW" in seen_urls[0]


def test_evm_token_transfer_log_import_credits_verified_wallet(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    monkeypatch.setenv("PICOIN_MARKETPLACE_ETH_CONFIRMATIONS", "3")
    monkeypatch.setenv("PICOIN_MARKETPLACE_EVM_ESCROW_ADDRESS", "0x2222222222222222222222222222222222222222")
    client = TestClient(marketplace_api.api)

    account = client.post("/accounts", json={"email": "erc20@example.com"}).json()
    wallet = client.post(
        f"/accounts/{account['account_id']}/wallets",
        json={
            "chain_code": "ethereum",
            "address": "0x1111111111111111111111111111111111111111",
        },
    ).json()
    client.post(f"/wallets/{wallet['wallet_id']}/verify")
    token = client.post(
        "/tokens",
        json={
            "chain_code": "ethereum",
            "token_symbol": "USDC",
            "display_name": "USD Coin",
            "decimals": 6,
            "token_type": "erc20",
            "contract_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "pico_rate": 1,
        },
    ).json()
    assert token["token_symbol"] == "USDC"

    transfer_log = {
        "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "topics": [
            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
            evm_topic("0x1111111111111111111111111111111111111111"),
            evm_topic("0x2222222222222222222222222222222222222222"),
        ],
        "data": hex(25_000_000),
        "transactionHash": "0x" + ("b" * 64),
        "blockNumber": hex(100),
        "blockHash": "0x" + ("c" * 64),
        "logIndex": hex(4),
    }

    imported = client.post(
        "/scanner/evm/import-token-transfers",
        json={
            "chain_code": "ethereum",
            "token_symbol": "USDC",
            "logs": [transfer_log],
            "latest_block_number": 102,
        },
    ).json()

    assert imported["imported"] == 1
    assert imported["confirmation_result"]["credited"] == 1

    balances = client.get(f"/accounts/{account['account_id']}/balances").json()
    usdc_balance = next(row for row in balances if row["token_symbol"] == "USDC")
    assert usdc_balance["available_base_units"] == "25000000"
    assert usdc_balance["available"] == "25"


def test_evm_token_transfer_poll_uses_rpc_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    monkeypatch.setenv("PICOIN_MARKETPLACE_ETH_CONFIRMATIONS", "1")
    monkeypatch.setenv("PICOIN_MARKETPLACE_EVM_ESCROW_ADDRESS", "0x2222222222222222222222222222222222222222")
    client = TestClient(marketplace_api.api)

    account = client.post("/accounts", json={"email": "poll-erc20@example.com"}).json()
    wallet = client.post(
        f"/accounts/{account['account_id']}/wallets",
        json={"chain_code": "ethereum", "address": "0x1111111111111111111111111111111111111111"},
    ).json()
    client.post(f"/wallets/{wallet['wallet_id']}/verify")
    client.post(
        "/tokens",
        json={
            "chain_code": "ethereum",
            "token_symbol": "GPU",
            "display_name": "GPU Token",
            "decimals": 18,
            "token_type": "erc20",
            "contract_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "pico_rate": 2,
        },
    )

    calls = []

    def fake_rpc(rpc_url, method, params):
        calls.append((rpc_url, method, params))
        if method == "eth_getLogs":
            return [
                {
                    "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "topics": [
                        "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                        evm_topic("0x1111111111111111111111111111111111111111"),
                        evm_topic("0x2222222222222222222222222222222222222222"),
                    ],
                    "data": hex(10**18),
                    "transactionHash": "0x" + ("d" * 64),
                    "blockNumber": hex(77),
                    "blockHash": "0x" + ("e" * 64),
                    "logIndex": "0x0",
                }
            ]
        raise AssertionError(method)

    monkeypatch.setattr("picoin_marketplace.marketplace.json_rpc_call", fake_rpc)

    result = client.post(
        "/scanner/evm/poll-token-transfers",
        json={
            "chain_code": "ethereum",
            "token_symbol": "GPU",
            "rpc_url": "https://rpc.example",
            "from_block": 77,
            "to_block": 77,
        },
    ).json()

    assert result["imported"] == 1
    assert calls[0][1] == "eth_getLogs"
    assert calls[0][2][0]["fromBlock"] == "0x4d"
    balances = client.get(f"/accounts/{account['account_id']}/balances").json()
    gpu_balance = next(row for row in balances if row["token_symbol"] == "GPU")
    assert gpu_balance["available_base_units"] == str(10**18)


def test_evm_native_transfer_poll_scans_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SEED_DEFAULT_POOLS", "0")
    monkeypatch.setenv("PICOIN_MARKETPLACE_ETH_CONFIRMATIONS", "1")
    monkeypatch.setenv("PICOIN_MARKETPLACE_EVM_ESCROW_ADDRESS", "0x2222222222222222222222222222222222222222")
    client = TestClient(marketplace_api.api)

    account = client.post("/accounts", json={"email": "native@example.com"}).json()
    wallet = client.post(
        f"/accounts/{account['account_id']}/wallets",
        json={"chain_code": "ethereum", "address": "0x1111111111111111111111111111111111111111"},
    ).json()
    client.post(f"/wallets/{wallet['wallet_id']}/verify")

    def fake_rpc(rpc_url, method, params):
        if method == "eth_getBlockByNumber":
            return {
                "number": hex(88),
                "hash": "0x" + ("f" * 64),
                "transactions": [
                    {
                        "hash": "0x" + ("1" * 64),
                        "from": "0x1111111111111111111111111111111111111111",
                        "to": "0x2222222222222222222222222222222222222222",
                        "value": hex(2 * 10**18),
                        "blockNumber": hex(88),
                        "blockHash": "0x" + ("f" * 64),
                    },
                    {
                        "hash": "0x" + ("2" * 64),
                        "from": "0x1111111111111111111111111111111111111111",
                        "to": "0x3333333333333333333333333333333333333333",
                        "value": hex(10**18),
                        "blockNumber": hex(88),
                    },
                ],
            }
        raise AssertionError(method)

    monkeypatch.setattr("picoin_marketplace.marketplace.json_rpc_call", fake_rpc)

    result = client.post(
        "/scanner/evm/poll-native-transfers",
        json={
            "chain_code": "ethereum",
            "token_symbol": "ETH",
            "rpc_url": "https://rpc.example",
            "from_block": 88,
            "to_block": 88,
        },
    ).json()

    assert result["imported"] == 1
    assert result["skipped"] == 1
    assert result["confirmation_result"]["credited"] == 1
    balances = client.get(f"/accounts/{account['account_id']}/balances").json()
    eth_balance = next(row for row in balances if row["token_symbol"] == "ETH")
    assert eth_balance["available_base_units"] == str(2 * 10**18)
