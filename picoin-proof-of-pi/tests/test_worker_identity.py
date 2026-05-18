from pathlib import Path

from miner.client import load_or_register_identity as load_or_register_miner_identity
from validator.client import load_or_register_identity as load_or_register_validator_identity


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

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
