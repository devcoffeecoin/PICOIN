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


def test_mine_once_treats_competitive_round_waiting_as_idle(monkeypatch, capsys):
    task = {
        "task_id": "task_late",
        "status": "assigned",
        "range_start": 10,
        "range_end": 11,
        "algorithm": "bbp_hex_v1",
    }
    challenge = {"accepted": True, "samples": [{"position": 10}]}
    waiting_response = {
        "accepted": False,
        "status": "competitive_round_waiting",
        "message": "competitive round already has pending candidate task_first",
        "block": None,
        "validation": {
            "pending_task_id": "task_first",
            "pending_job_id": "job_first",
        },
    }

    monkeypatch.setattr(client, "get_task_for_identity", lambda server, identity: task)
    monkeypatch.setattr(client, "calculate_segment_with_workers", lambda start, end, algorithm, workers: "ab")
    monkeypatch.setattr(client, "hash_result", lambda segment, start, end, algorithm: "result_hash")
    monkeypatch.setattr(client, "merkle_root", lambda segment, start: "root")
    monkeypatch.setattr(client, "commit_result", lambda server, task_arg, identity, result_hash, root, compute_ms: challenge)
    monkeypatch.setattr(client, "reveal_samples", lambda server, task_arg, identity, segment, root, challenge_arg: waiting_response)

    mined = client.mine_once(
        "http://node",
        {"miner_id": "miner_1", "private_key": "unused"},
        workers=1,
    )

    captured = capsys.readouterr()
    assert mined is False
    assert "Competitive round already has pending candidate: task_first" in captured.out
    assert "Rejected:" not in captured.out
    assert captured.err == ""
