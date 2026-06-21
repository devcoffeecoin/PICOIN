from __future__ import annotations

import pytest

from picoin_marketplace.models import HardwareType
from picoin_marketplace.worker_agent import WorkerAgentConfig, config_from_env, run_once


def test_worker_agent_registers_and_sends_heartbeat(monkeypatch):
    calls = []

    def fake_json_post(url, payload):
        calls.append((url, payload))
        if url.endswith("/workers/register"):
            return {"worker": {"worker_id": "worker-gpu-1"}, "listing": {"listing_id": "listing-1"}}
        if url.endswith("/workers/worker-gpu-1/heartbeat"):
            return {"worker": {"worker_id": "worker-gpu-1", "status": "online"}, "listing": {"units_available": 3}}
        raise AssertionError(url)

    monkeypatch.setattr("picoin_marketplace.worker_agent.json_post", fake_json_post)
    config = WorkerAgentConfig(
        marketplace_url="http://marketplace.local",
        worker_id="worker-gpu-1",
        provider_id="provider-gpu-1",
        provider_wallet="PI_PROVIDER_GPU",
        pool_id="pool_gpu_raven",
        hardware_type=HardwareType.GPU,
        units_total=3,
        units_available=3,
        price_pi_per_hour=2.0,
        capabilities=["cuda"],
    )

    result = run_once(config)

    assert result["worker_id"] == "worker-gpu-1"
    assert calls[0] == (
        "http://marketplace.local/workers/register",
        {
            "worker_id": "worker-gpu-1",
            "provider_id": "provider-gpu-1",
            "provider_wallet": "PI_PROVIDER_GPU",
            "pool_id": "pool_gpu_raven",
            "hardware_type": "gpu",
            "units_total": 3,
            "price_pi_per_hour": 2.0,
            "min_booking_minutes": 30,
            "capabilities": ["cuda"],
            "endpoint_url": None,
            "agent_version": "0.1.0",
        },
    )
    assert calls[1][0] == "http://marketplace.local/workers/worker-gpu-1/heartbeat"
    assert calls[1][1]["units_available"] == 3


def test_worker_agent_requires_worker_id_without_registration(monkeypatch):
    monkeypatch.setattr("picoin_marketplace.worker_agent.json_post", lambda url, payload: {})
    config = WorkerAgentConfig(pool_id="pool_gpu_raven", worker_id=None)

    with pytest.raises(ValueError, match="worker_id is required"):
        run_once(config, register=False)


def test_worker_agent_config_from_env(monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_URL", "http://marketplace.local")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_ID", "worker-cpu-1")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_POOL_ID", "pool_cpu_monero")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_HARDWARE_TYPE", "cpu")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_UNITS_TOTAL", "8")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_UNITS_AVAILABLE", "7")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_PRICE_PI_PER_HOUR", "0.25")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_CAPABILITIES", "randomx,cpu")

    config = config_from_env()

    assert config.marketplace_url == "http://marketplace.local"
    assert config.worker_id == "worker-cpu-1"
    assert config.pool_id == "pool_cpu_monero"
    assert config.hardware_type == HardwareType.CPU
    assert config.units_total == 8
    assert config.units_available == 7
    assert config.price_pi_per_hour == 0.25
    assert config.capabilities == ["randomx", "cpu"]
