from __future__ import annotations

from pathlib import Path

from picoin_marketplace.maintenance_worker import MaintenanceWorkerConfig, config_from_env, run_maintenance_once


class FakeMarketplace:
    def __init__(self):
        self.calls = []

    def expire_stale_workers(self, *, stale_after_seconds: int, limit: int):
        self.calls.append((stale_after_seconds, limit))
        return {"expired": 2}


def test_maintenance_worker_expires_stale_workers():
    fake = FakeMarketplace()
    config = MaintenanceWorkerConfig(
        state_dir=Path("."),
        stale_after_seconds=90,
        limit=25,
    )

    result = run_maintenance_once(fake, config)  # type: ignore[arg-type]

    assert fake.calls == [(90, 25)]
    assert result["service"] == "picoin-marketplace-maintenance"
    assert result["result"] == {"expired": 2}


def test_maintenance_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_MAINTENANCE_INTERVAL_SECONDS", "11")
    monkeypatch.setenv("PICOIN_MARKETPLACE_WORKER_STALE_AFTER_SECONDS", "77")
    monkeypatch.setenv("PICOIN_MARKETPLACE_MAINTENANCE_LIMIT", "44")

    config = config_from_env()

    assert config.state_dir == tmp_path
    assert config.interval_seconds == 11
    assert config.stale_after_seconds == 77
    assert config.limit == 44
