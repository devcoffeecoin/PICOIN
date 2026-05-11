import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests
import uvicorn

from app.tools.bootstrap_testnet import main as bootstrap_testnet_main
from app.tools.reset_testnet import main as reset_testnet_main
from app.tools.run_testnet_cycle import main as run_testnet_cycle_main
from app.tools.run_testnet_multi_miner import main as run_testnet_multi_miner_main
from miner.client import command_mine, command_register as miner_register, command_stats as miner_stats
from validator.client import command_register as validator_register, command_validate


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

from app.core.settings import PROJECT_NAME


DEFAULT_SERVER_URL = os.getenv("PICOIN_SERVER", "http://127.0.0.1:8000")
DEFAULT_HOST = os.getenv("PICOIN_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("PICOIN_PORT", "8000"))


def normalize_server_url(server: str) -> str:
    return server.rstrip("/")


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def get_json(server_url: str, path: str) -> Any:
    response = requests.get(f"{normalize_server_url(server_url)}{path}", timeout=20)
    response.raise_for_status()
    return response.json()


def command_node_start(args: argparse.Namespace) -> int:
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


def command_node_status(args: argparse.Namespace) -> int:
    server_url = normalize_server_url(args.server)
    print_json(
        {
            "root": get_json(server_url, "/"),
            "health": get_json(server_url, "/health"),
            "status": get_json(server_url, "/node/status"),
            "events": get_json(server_url, "/events?limit=5"),
        }
    )
    return 0


def command_node_audit(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/audit/full"))
    return 0


def command_node_protocol(args: argparse.Namespace) -> int:
    print_json(get_json(args.server, "/protocol"))
    return 0


def command_testnet_reset(args: argparse.Namespace) -> int:
    forwarded = ["--yes"]
    if args.keep_identities:
        forwarded.append("--keep-identities")
    _run_tool_main(reset_testnet_main, forwarded)
    return 0


def command_testnet_bootstrap(args: argparse.Namespace) -> int:
    forwarded = [
        "--server",
        args.server,
        "--identity-dir",
        str(args.identity_dir),
        "--miner-faucet",
        str(args.miner_faucet),
    ]
    _run_tool_main(bootstrap_testnet_main, forwarded)
    return 0


def command_testnet_cycle(args: argparse.Namespace) -> int:
    forwarded = [
        "--server",
        args.server,
        "--miner",
        str(args.miner_identity),
        "--validator-one",
        str(args.validator_one_identity),
        "--validator-two",
        str(args.validator_two_identity),
        "--validator-three",
        str(args.validator_three_identity),
        "--workers",
        str(args.workers),
    ]
    _run_tool_main(run_testnet_cycle_main, forwarded)
    return 0


def command_testnet_continuous(args: argparse.Namespace) -> int:
    forwarded = [
        "--server",
        args.server,
        "--identity-dir",
        str(args.identity_dir),
        "--miners",
        str(args.miners),
        "--loops",
        str(args.loops),
        "--workers",
        str(args.workers),
        "--sleep",
        str(args.sleep),
        "--faucet",
        str(args.faucet),
    ]
    if not args.retro_audit:
        forwarded.append("--no-retro-audit")
    _run_tool_main(run_testnet_multi_miner_main, forwarded)
    return 0


def _run_tool_main(main_func: Any, argv: list[str]) -> None:
    import sys

    old_argv = sys.argv
    try:
        sys.argv = [old_argv[0], *argv]
        main_func()
    finally:
        sys.argv = old_argv


def add_node_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("node", help="Run or inspect a local Picoin coordinator node")
    node_subparsers = parser.add_subparsers(dest="node_command", required=True)

    start_parser = node_subparsers.add_parser("start", help="Start the FastAPI coordinator")
    start_parser.add_argument("--host", default=DEFAULT_HOST)
    start_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    start_parser.add_argument("--reload", action="store_true")
    start_parser.add_argument("--log-level", default="info")
    start_parser.set_defaults(func=command_node_start)

    status_parser = node_subparsers.add_parser("status", help="Show local node health, status and recent events")
    status_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    status_parser.set_defaults(func=command_node_status)

    audit_parser = node_subparsers.add_parser("audit", help="Run full economic audit")
    audit_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    audit_parser.set_defaults(func=command_node_audit)

    protocol_parser = node_subparsers.add_parser("protocol", help="Show active protocol")
    protocol_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    protocol_parser.set_defaults(func=command_node_protocol)


def add_miner_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("miner", help="Register or run a local miner")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--identity", type=Path, default=Path("miner_identity.json"))
    miner_subparsers = parser.add_subparsers(dest="miner_command", required=True)

    register_parser = miner_subparsers.add_parser("register", help="Register a miner identity")
    register_parser.add_argument("--name", default="local-miner")
    register_parser.add_argument("--overwrite", action="store_true")
    register_parser.set_defaults(func=miner_register)

    mine_parser = miner_subparsers.add_parser("mine", help="Mine Proof-of-Pi tasks")
    mine_parser.add_argument("--once", action="store_true")
    mine_parser.add_argument("--loops", type=int, default=1)
    mine_parser.add_argument("--sleep", type=float, default=1.0)
    mine_parser.add_argument("--workers", type=int, default=1)
    mine_parser.set_defaults(func=command_mine)

    stats_parser = miner_subparsers.add_parser("stats", help="Show miner stats")
    stats_parser.set_defaults(func=miner_stats)


def add_validator_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("validator", help="Register or run a local validator")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--identity", type=Path, default=Path("validator_identity.json"))
    validator_subparsers = parser.add_subparsers(dest="validator_command", required=True)

    register_parser = validator_subparsers.add_parser("register", help="Register a validator identity")
    register_parser.add_argument("--name", default="local-validator")
    register_parser.add_argument("--overwrite", action="store_true")
    register_parser.set_defaults(func=validator_register)

    validate_parser = validator_subparsers.add_parser("validate", help="Validate pending jobs")
    validate_parser.add_argument("--once", action="store_true")
    validate_parser.add_argument("--loops", type=int, default=1)
    validate_parser.add_argument("--sleep", type=float, default=1.0)
    validate_parser.set_defaults(func=command_validate)


def add_testnet_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("testnet", help="Manage local demo testnet")
    testnet_subparsers = parser.add_subparsers(dest="testnet_command", required=True)

    reset_parser = testnet_subparsers.add_parser("reset", help="Reset SQLite and demo files")
    reset_parser.add_argument("--keep-identities", action="store_true")
    reset_parser.set_defaults(func=command_testnet_reset)

    bootstrap_parser = testnet_subparsers.add_parser("bootstrap", help="Create demo miner and validators")
    bootstrap_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    bootstrap_parser.add_argument("--identity-dir", type=Path, default=Path("data/testnet/identities"))
    bootstrap_parser.add_argument("--miner-faucet", type=float, default=31.416)
    bootstrap_parser.set_defaults(func=command_testnet_bootstrap)

    cycle_parser = testnet_subparsers.add_parser("cycle", help="Mine once and validate with three demo validators")
    cycle_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    cycle_parser.add_argument("--miner-identity", type=Path, default=Path("data/testnet/identities/miner-alice.json"))
    cycle_parser.add_argument("--validator-one-identity", type=Path, default=Path("data/testnet/identities/validator-one.json"))
    cycle_parser.add_argument("--validator-two-identity", type=Path, default=Path("data/testnet/identities/validator-two.json"))
    cycle_parser.add_argument("--validator-three-identity", type=Path, default=Path("data/testnet/identities/validator-three.json"))
    cycle_parser.add_argument("--workers", type=int, default=1)
    cycle_parser.set_defaults(func=command_testnet_cycle)

    continuous_parser = testnet_subparsers.add_parser("continuous", help="Run continuous mining with multiple demo miners")
    continuous_parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    continuous_parser.add_argument("--identity-dir", type=Path, default=Path("data/testnet/identities"))
    continuous_parser.add_argument("--miners", type=int, default=3)
    continuous_parser.add_argument("--loops", type=int, default=3)
    continuous_parser.add_argument("--workers", type=int, default=1)
    continuous_parser.add_argument("--sleep", type=float, default=0.5)
    continuous_parser.add_argument("--faucet", type=float, default=31.416)
    continuous_parser.add_argument("--retro-audit", action="store_true", default=True)
    continuous_parser.add_argument("--no-retro-audit", action="store_false", dest="retro_audit")
    continuous_parser.set_defaults(func=command_testnet_continuous)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="picoin", description=f"{PROJECT_NAME} local node CLI")
    parser.add_argument("--version", action="store_true", help="Show CLI version context and exit")
    subparsers = parser.add_subparsers(dest="command")
    add_node_parser(subparsers)
    add_miner_parser(subparsers)
    add_validator_parser(subparsers)
    add_testnet_parser(subparsers)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.version:
        print_json({"project": PROJECT_NAME, "cli": "picoin", "mode": "local-node"})
        raise SystemExit(0)
    if not hasattr(args, "func"):
        parser.print_help()
        raise SystemExit(2)
    try:
        raise SystemExit(args.func(args))
    except requests.RequestException as exc:
        print(f"Network/API error: {exc}")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
