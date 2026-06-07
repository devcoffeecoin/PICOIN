import miner.client as client


class DummyResponse:
    def __init__(self, status_code=200, data=None, text="", headers=None, reason="Too Many Requests"):
        self.status_code = status_code
        self._data = data
        self.text = text
        self.headers = headers or {}
        self.reason = reason

    def raise_for_status(self):
        raise AssertionError("429 task responses should not call raise_for_status")

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def test_tasks_next_429_becomes_task_unavailable(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        assert url == "http://node/tasks/next"
        return DummyResponse(
            status_code=429,
            data={"detail": "competitive round is waiting for validation; retry after next block"},
            headers={"Retry-After": "7"},
        )

    monkeypatch.setattr(client.requests, "get", fake_get)

    try:
        client.get_task_for_identity("http://node", {"miner_id": "miner_1"})
    except client.TaskUnavailable as exc:
        assert "waiting for validation" in exc.detail
        assert exc.retry_after_seconds == 7
    else:
        raise AssertionError("expected TaskUnavailable")


def test_mine_once_treats_429_as_idle_without_crashing(monkeypatch, capsys):
    def fake_get(url, params=None, timeout=None):
        return DummyResponse(
            status_code=429,
            data={"detail": "miner has too many active tasks"},
        )

    monkeypatch.setattr(client.requests, "get", fake_get)

    mined = client.mine_once(
        "http://node",
        {"miner_id": "miner_1", "private_key": "unused"},
        workers=1,
    )

    captured = capsys.readouterr()
    assert mined is False
    assert "No mining task available yet" in captured.out
    assert "miner has too many active tasks" in captured.out
    assert captured.err == ""
