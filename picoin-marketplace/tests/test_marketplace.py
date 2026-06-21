from __future__ import annotations

from fastapi.testclient import TestClient

from picoin_marketplace import api as marketplace_api


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
