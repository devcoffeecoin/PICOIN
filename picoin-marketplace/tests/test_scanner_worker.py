from __future__ import annotations

from pathlib import Path

from picoin_marketplace.models import EvmNativeTransferPollRequest, EvmTokenTransferPollRequest, PicoinNodePollRequest
from picoin_marketplace.scanner_worker import ScannerWorkerConfig, config_from_env, run_scanner_once


class FakeMarketplace:
    def __init__(self):
        self.calls = []

    def poll_picoin_node(self, request: PicoinNodePollRequest):
        self.calls.append(("picoin", request))
        return {"imported": 1}

    def poll_evm_token_transfers(self, request: EvmTokenTransferPollRequest):
        self.calls.append(("evm_tokens", request))
        return {"imported": 2}

    def poll_evm_native_transfers(self, request: EvmNativeTransferPollRequest):
        self.calls.append(("evm_native", request))
        return {"imported": 3}


def test_scanner_worker_runs_enabled_scanners():
    fake = FakeMarketplace()
    config = ScannerWorkerConfig(
        state_dir=Path("."),
        picoin_enabled=True,
        picoin_node_url="http://picoin.local:8000",
        picoin_limit=25,
        evm_token_enabled=True,
        evm_native_enabled=True,
        evm_rpc_url="https://rpc.example",
        evm_token_symbol="USDC",
    )

    result = run_scanner_once(fake, config)  # type: ignore[arg-type]

    assert not result["errors"]
    assert [call[0] for call in fake.calls] == ["picoin", "evm_tokens", "evm_native"]
    assert fake.calls[0][1].node_url == "http://picoin.local:8000"
    assert fake.calls[0][1].limit == 25
    assert fake.calls[1][1].token_symbol == "USDC"
    assert fake.calls[2][1].rpc_url == "https://rpc.example"


def test_scanner_worker_continues_after_scanner_error():
    class PartiallyBrokenMarketplace(FakeMarketplace):
        def poll_picoin_node(self, request: PicoinNodePollRequest):
            raise RuntimeError("picoin offline")

    fake = PartiallyBrokenMarketplace()
    config = ScannerWorkerConfig(
        state_dir=Path("."),
        picoin_enabled=True,
        evm_token_enabled=True,
        evm_rpc_url="https://rpc.example",
    )

    result = run_scanner_once(fake, config)  # type: ignore[arg-type]

    assert result["errors"] == [{"scanner": "picoin_history", "error": "picoin offline"}]
    assert [call[0] for call in fake.calls] == ["evm_tokens"]


def test_scanner_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PICOIN_MARKETPLACE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PICOIN_MARKETPLACE_SCANNER_INTERVAL_SECONDS", "7")
    monkeypatch.setenv("PICOIN_MARKETPLACE_SCANNER_PICOIN_ENABLED", "0")
    monkeypatch.setenv("PICOIN_MARKETPLACE_EVM_RPC_URL", "https://rpc.example")
    monkeypatch.setenv("PICOIN_MARKETPLACE_SCANNER_EVM_NATIVE_ENABLED", "1")
    monkeypatch.setenv("PICOIN_MARKETPLACE_SCANNER_EVM_TOKEN_SYMBOL", "USDC")

    config = config_from_env()

    assert config.state_dir == tmp_path
    assert config.interval_seconds == 7
    assert config.picoin_enabled is False
    assert config.evm_token_enabled is True
    assert config.evm_native_enabled is True
    assert config.evm_rpc_url == "https://rpc.example"
    assert config.evm_token_symbol == "USDC"
