import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pool_worker


def test_mine_once_returns_false_on_pool_timeout(monkeypatch):
    def fake_get(*_args, **_kwargs):
        raise requests.ReadTimeout("local pool timeout")

    monkeypatch.setattr(pool_worker.requests, "get", fake_get)

    assert pool_worker.mine_once("http://127.0.0.1:9321", "worker-1", "token", 5) is False

