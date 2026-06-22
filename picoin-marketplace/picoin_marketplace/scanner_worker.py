from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .marketplace import DEFAULT_PICOIN_NODE_URL, DEFAULT_STATE_DIR, Marketplace
from .models import EvmNativeTransferPollRequest, EvmTokenTransferPollRequest, PicoinNodePollRequest, utc_now


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_symbols(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    symbols = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    return symbols or default


@dataclass(frozen=True)
class ScannerWorkerConfig:
    state_dir: Path = DEFAULT_STATE_DIR
    interval_seconds: int = 30
    picoin_enabled: bool = True
    picoin_node_url: str = DEFAULT_PICOIN_NODE_URL
    picoin_address: str | None = None
    picoin_limit: int = 50
    evm_token_enabled: bool = False
    evm_native_enabled: bool = False
    evm_chain_code: str = "ethereum"
    evm_rpc_url: str | None = None
    evm_token_symbol: str | None = None
    evm_token_symbols: tuple[str, ...] = ("USDT", "USDC")
    evm_native_token_symbol: str = "ETH"
    evm_batch_size: int = 500
    evm_native_batch_size: int = 100


def config_from_env() -> ScannerWorkerConfig:
    evm_rpc_url = os.getenv("PICOIN_MARKETPLACE_EVM_RPC_URL") or os.getenv("PICOIN_MARKETPLACE_ETH_RPC_URL")
    legacy_token_symbol = os.getenv("PICOIN_MARKETPLACE_SCANNER_EVM_TOKEN_SYMBOL")
    return ScannerWorkerConfig(
        state_dir=Path(os.getenv("PICOIN_MARKETPLACE_STATE_DIR", str(DEFAULT_STATE_DIR))),
        interval_seconds=max(1, env_int("PICOIN_MARKETPLACE_SCANNER_INTERVAL_SECONDS", 30)),
        picoin_enabled=env_flag("PICOIN_MARKETPLACE_SCANNER_PICOIN_ENABLED", True),
        picoin_node_url=os.getenv("PICOIN_MARKETPLACE_PICOIN_NODE_URL", DEFAULT_PICOIN_NODE_URL),
        picoin_address=os.getenv("PICOIN_MARKETPLACE_SCANNER_PICOIN_ADDRESS") or None,
        picoin_limit=max(1, env_int("PICOIN_MARKETPLACE_SCANNER_PICOIN_LIMIT", 50)),
        evm_token_enabled=env_flag("PICOIN_MARKETPLACE_SCANNER_EVM_TOKENS_ENABLED", bool(evm_rpc_url)),
        evm_native_enabled=env_flag("PICOIN_MARKETPLACE_SCANNER_EVM_NATIVE_ENABLED", False),
        evm_chain_code=os.getenv("PICOIN_MARKETPLACE_SCANNER_EVM_CHAIN_CODE", "ethereum"),
        evm_rpc_url=evm_rpc_url,
        evm_token_symbol=legacy_token_symbol or None,
        evm_token_symbols=env_symbols(
            "PICOIN_MARKETPLACE_SCANNER_EVM_TOKEN_SYMBOLS",
            (legacy_token_symbol.upper(),) if legacy_token_symbol else ("USDT", "USDC"),
        ),
        evm_native_token_symbol=os.getenv("PICOIN_MARKETPLACE_SCANNER_EVM_NATIVE_TOKEN_SYMBOL", "ETH"),
        evm_batch_size=max(1, env_int("PICOIN_MARKETPLACE_SCANNER_EVM_BATCH_SIZE", 500)),
        evm_native_batch_size=max(1, env_int("PICOIN_MARKETPLACE_SCANNER_EVM_NATIVE_BATCH_SIZE", 100)),
    )


def run_scanner_once(marketplace: Marketplace, config: ScannerWorkerConfig) -> dict[str, Any]:
    started_at = utc_now()
    runs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    if config.picoin_enabled:
        try:
            runs.append(
                {
                    "scanner": "picoin_history",
                    "result": marketplace.poll_picoin_node(
                        PicoinNodePollRequest(
                            node_url=config.picoin_node_url,
                            address=config.picoin_address,
                            limit=config.picoin_limit,
                            confirmed_only=True,
                        )
                    ),
                }
            )
        except Exception as exc:  # pragma: no cover - exercised by integration logs
            errors.append({"scanner": "picoin_history", "error": str(exc)})

    if config.evm_token_enabled:
        token_symbols: tuple[str | None, ...] = config.evm_token_symbols or (config.evm_token_symbol,)
        for token_symbol in token_symbols:
            scanner_name = f"evm_erc20_{token_symbol}" if token_symbol else "evm_erc20"
            try:
                runs.append(
                    {
                        "scanner": scanner_name,
                        "result": marketplace.poll_evm_token_transfers(
                            EvmTokenTransferPollRequest(
                                chain_code=config.evm_chain_code,
                                token_symbol=token_symbol,
                                rpc_url=config.evm_rpc_url,
                                batch_size=config.evm_batch_size,
                            )
                        ),
                    }
                )
            except Exception as exc:  # pragma: no cover - exercised by integration logs
                errors.append({"scanner": scanner_name, "error": str(exc)})

    if config.evm_native_enabled:
        try:
            runs.append(
                {
                    "scanner": "evm_native",
                    "result": marketplace.poll_evm_native_transfers(
                        EvmNativeTransferPollRequest(
                            chain_code=config.evm_chain_code,
                            token_symbol=config.evm_native_token_symbol,
                            rpc_url=config.evm_rpc_url,
                            batch_size=config.evm_native_batch_size,
                        )
                    ),
                }
            )
        except Exception as exc:  # pragma: no cover - exercised by integration logs
            errors.append({"scanner": "evm_native", "error": str(exc)})

    return {
        "service": "picoin-marketplace-scanner",
        "started_at": started_at.isoformat(),
        "finished_at": utc_now().isoformat(),
        "runs": runs,
        "errors": errors,
    }


def run_forever(config: ScannerWorkerConfig) -> None:
    marketplace = Marketplace(config.state_dir)
    while True:
        print(json.dumps(run_scanner_once(marketplace, config), sort_keys=True), flush=True)
        time.sleep(config.interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Picoin Marketplace deposit scanners.")
    parser.add_argument("--once", action="store_true", help="run one scan tick and exit")
    parser.add_argument("--interval-seconds", type=int, default=None)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--picoin-node-url", default=None)
    parser.add_argument("--picoin-address", default=None)
    parser.add_argument("--picoin-limit", type=int, default=None)
    parser.add_argument("--no-picoin", action="store_true")
    parser.add_argument("--evm-rpc-url", default=None)
    parser.add_argument("--evm-chain-code", default=None)
    parser.add_argument("--evm-token-symbol", default=None)
    parser.add_argument("--evm-token-symbols", default=None)
    parser.add_argument("--evm-tokens", action="store_true")
    parser.add_argument("--evm-native", action="store_true")
    parser.add_argument("--evm-batch-size", type=int, default=None)
    parser.add_argument("--evm-native-batch-size", type=int, default=None)
    return parser


def config_from_args(args: argparse.Namespace) -> ScannerWorkerConfig:
    base = config_from_env()
    if args.evm_token_symbols:
        token_symbols = tuple(item.strip().upper() for item in args.evm_token_symbols.split(",") if item.strip())
    elif args.evm_token_symbol:
        token_symbols = (args.evm_token_symbol.upper(),)
    else:
        token_symbols = base.evm_token_symbols
    return ScannerWorkerConfig(
        state_dir=Path(args.state_dir) if args.state_dir else base.state_dir,
        interval_seconds=max(1, args.interval_seconds or base.interval_seconds),
        picoin_enabled=False if args.no_picoin else base.picoin_enabled,
        picoin_node_url=args.picoin_node_url or base.picoin_node_url,
        picoin_address=args.picoin_address or base.picoin_address,
        picoin_limit=max(1, args.picoin_limit or base.picoin_limit),
        evm_token_enabled=True if args.evm_tokens else base.evm_token_enabled,
        evm_native_enabled=True if args.evm_native else base.evm_native_enabled,
        evm_chain_code=args.evm_chain_code or base.evm_chain_code,
        evm_rpc_url=args.evm_rpc_url or base.evm_rpc_url,
        evm_token_symbol=args.evm_token_symbol or base.evm_token_symbol,
        evm_token_symbols=token_symbols,
        evm_native_token_symbol=base.evm_native_token_symbol,
        evm_batch_size=max(1, args.evm_batch_size or base.evm_batch_size),
        evm_native_batch_size=max(1, args.evm_native_batch_size or base.evm_native_batch_size),
    )


def main() -> None:
    args = build_parser().parse_args()
    config = config_from_args(args)
    marketplace = Marketplace(config.state_dir)
    if args.once:
        print(json.dumps(run_scanner_once(marketplace, config), indent=2, sort_keys=True))
        return
    run_forever(config)


if __name__ == "__main__":
    main()
