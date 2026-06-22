from __future__ import annotations

from picoin_marketplace import real_miner_agent
from picoin_marketplace.models import HardwareType
from picoin_marketplace.real_miner_agent import RealMinerConfig, config_from_env, run_miner_once


class FakeProcess:
    pid = 12345

    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


def test_real_miner_autodiscovers_monero_pool_and_reports_xmrig(monkeypatch):
    posts = []
    popen_calls = []
    fake_process = FakeProcess()

    def fake_get(url):
        if "/pools" in url:
            assert "hardware_type=cpu" in url
            assert "paired_coin=MONERO" in url
            return [{"pool_id": "pool_cpu_monero", "pair_symbol": "PICOIN/MONERO"}]
        if url.endswith("/workers/real-monero-1/assignments?active_only=true&limit=100"):
            return [{"assignment_id": "assign-1", "booking_id": "booking-1"}]
        raise AssertionError(url)

    def fake_post(url, payload):
        posts.append((url, payload))
        if url.endswith("/workers/register"):
            return {"worker": {"worker_id": "real-monero-1"}, "listing": {"listing_id": "listing-1"}}
        if url.endswith("/workers/real-monero-1/heartbeat"):
            return {"worker": {"worker_id": "real-monero-1", "status": "online"}}
        if url.endswith("/workers/real-monero-1/assignments/booking-1/reports"):
            return {"report_id": "report-1", "status": payload["status"]}
        raise AssertionError(url)

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return fake_process

    monkeypatch.setattr("picoin_marketplace.worker_agent.json_get", fake_get)
    monkeypatch.setattr("picoin_marketplace.worker_agent.json_post", fake_post)
    monkeypatch.setattr(real_miner_agent.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(real_miner_agent.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        real_miner_agent,
        "read_miner_api",
        lambda config: {
            "algo": "rx/0",
            "worker_id": "xmr-worker",
            "hashrate": {"total": [321.5, None, None]},
            "results": {"shares_good": 12, "shares_total": 13},
            "uptime": 90,
        },
    )

    config = RealMinerConfig(
        marketplace_url="http://marketplace.local",
        worker_id="real-monero-1",
        provider_id="provider-cpu-1",
        provider_wallet="PI_PROVIDER_CPU",
        hardware_type=HardwareType.CPU,
        paired_coin="MONERO",
        units_total=2,
        price_pi_per_hour=0.5,
        miner_command=["xmrig", "-o", "pool.supportxmr.com:443"],
        miner_api_url="http://127.0.0.1:18088/2/summary",
        warmup_seconds=0,
    )

    result = run_miner_once(config)

    assert result["pool_id"] == "pool_cpu_monero"
    assert result["worker_id"] == "real-monero-1"
    assert result["report_count"] == 1
    assert popen_calls[0][0] == ["xmrig", "-o", "pool.supportxmr.com:443"]
    assert fake_process.terminated is True
    register_payload = posts[0][1]
    assert register_payload["pool_id"] == "pool_cpu_monero"
    assert register_payload["hardware_type"] == "cpu"
    heartbeat_payload = posts[1][1]
    assert heartbeat_payload["status"] == "online"
    assert heartbeat_payload["metrics"]["real_miner"] is True
    assert heartbeat_payload["metrics"]["pair_symbol"] == "PICOIN/MONERO"
    report_payload = posts[2][1]
    assert report_payload["reported_hashrate"] == 321.5
    assert report_payload["accepted_shares"] == 12
    assert report_payload["rejected_shares"] == 1
    assert report_payload["uptime_seconds"] == 90


def test_real_miner_config_from_env(monkeypatch):
    monkeypatch.setenv("PICOIN_MARKETPLACE_URL", "http://marketplace.local")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_ID", "real-monero-1")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_PROVIDER_WALLET", "PI_PROVIDER_CPU")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_HARDWARE_TYPE", "cpu")
    monkeypatch.setenv("PICOIN_MARKETPLACE_MINER_PAIRED_COIN", "monero")
    monkeypatch.setenv("PICOIN_MARKETPLACE_MINER_COMMAND", "xmrig -o pool.example:443 -u wallet")
    monkeypatch.setenv("PICOIN_MARKETPLACE_MINER_API_URL", "http://127.0.0.1:18088/2/summary")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_UNITS_TOTAL", "4")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_PRICE_PI_PER_HOUR", "0.75")

    config = config_from_env()

    assert config.marketplace_url == "http://marketplace.local"
    assert config.worker_id == "real-monero-1"
    assert config.provider_wallet == "PI_PROVIDER_CPU"
    assert config.hardware_type == HardwareType.CPU
    assert config.paired_coin == "MONERO"
    assert config.miner_command == ["xmrig", "-o", "pool.example:443", "-u", "wallet"]
    assert config.miner_api_url == "http://127.0.0.1:18088/2/summary"
    assert config.units_total == 4
    assert config.price_pi_per_hour == 0.75
