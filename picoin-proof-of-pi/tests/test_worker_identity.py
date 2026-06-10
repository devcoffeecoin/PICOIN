from pathlib import Path

from miner.client import load_or_register_identity as load_or_register_miner_identity
from miner.client import get_task_for_identity as get_miner_task_for_identity
from validator.client import get_job as get_validator_job
from validator.client import load_or_register_identity as load_or_register_validator_identity
from validator.client import normalize_node_address


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.content = b"{}"
        self.text = "{}"
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_miner_auto_registers_missing_identity(tmp_path, monkeypatch) -> None:
    identity_path = tmp_path / "miner-alice.json"

    def post(url: str, json: dict, timeout: int) -> _Response:
        assert url == "http://node/miners/register"
        assert json["name"] == "miner-alice"
        return _Response({"miner_id": "miner_auto", "name": json["name"]})

    monkeypatch.setattr("miner.client.requests.post", post)

    identity = load_or_register_miner_identity("http://node", identity_path)

    assert identity["miner_id"] == "miner_auto"
    assert identity["name"] == "miner-alice"
    assert identity_path.exists()


def test_validator_auto_registers_missing_identity(tmp_path, monkeypatch) -> None:
    identity_path = tmp_path / "validator-one.json"

    def post(url: str, json: dict, timeout: int) -> _Response:
        assert url == "http://node/validators/register"
        assert json["name"] == "validator-one"
        return _Response({"validator_id": "validator_auto", "name": json["name"]})

    monkeypatch.setattr("validator.client.requests.post", post)

    identity = load_or_register_validator_identity("http://node", identity_path)

    assert identity["validator_id"] == "validator_auto"
    assert identity["name"] == "validator-one"
    assert identity_path.exists()


def test_validator_job_poll_sends_identity_context(monkeypatch) -> None:
    identity = {
        "validator_id": "validator_restored",
        "name": "validator-one",
        "public_key": "ed25519:test-public-key",
    }

    def get(url: str, params: dict, timeout: int) -> _Response:
        assert url == "http://node/validation/jobs"
        assert params == {
            "validator_id": "validator_restored",
            "name": "validator-one",
            "public_key": "ed25519:test-public-key",
        }
        return _Response({})

    monkeypatch.setattr("validator.client.requests.get", get)

    assert get_validator_job("http://node", identity) == {}


def test_worker_http_timeout_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv("PICOIN_WORKER_HTTP_TIMEOUT_SECONDS", "75")
    identity = {
        "miner_id": "miner_restored",
        "name": "miner-one",
        "public_key": "ed25519:test-public-key",
    }
    captured: dict = {}

    def get(url: str, params: dict, timeout: float) -> _Response:
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return _Response({"task_id": "task_one", "status": "assigned"})

    monkeypatch.setattr("miner.client.requests.get", get)

    assert get_miner_task_for_identity("http://node", identity)["task_id"] == "task_one"
    assert captured == {
        "url": "http://node/tasks/next",
        "params": {
            "miner_id": "miner_restored",
            "name": "miner-one",
            "public_key": "ed25519:test-public-key",
        },
        "timeout": 75.0,
    }


def test_validator_node_address_normalizes_duplicate_scheme() -> None:
    assert normalize_node_address("http://http://170.64.193.147:8000/") == "http://170.64.193.147:8000"
    assert normalize_node_address("https://https://validator.example.com/") == "https://validator.example.com"
