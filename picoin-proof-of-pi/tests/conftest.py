from __future__ import annotations

import pytest

from app.services import mining as mining_service
from app.services import rewards as rewards_service


@pytest.fixture(autouse=True)
def legacy_default_mining_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mining_service, "MINING_TASK_MODE", "assigned")
    monkeypatch.setattr(mining_service, "BLOCK_MATURITY_DEPTH", 0)
    monkeypatch.setattr(rewards_service, "BLOCK_MATURITY_DEPTH", 0)
