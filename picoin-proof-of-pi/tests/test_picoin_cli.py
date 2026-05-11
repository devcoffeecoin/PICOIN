from pathlib import Path

from picoin.cli import build_parser, normalize_server_url


def test_picoin_cli_parses_node_start_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["node", "start"])

    assert args.command == "node"
    assert args.node_command == "start"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.reload is False


def test_picoin_cli_parses_miner_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "miner",
            "--server",
            "http://127.0.0.1:8000/",
            "--identity",
            "data/testnet/identities/miner-alice.json",
            "mine",
            "--once",
            "--workers",
            "2",
        ]
    )

    assert args.command == "miner"
    assert args.miner_command == "mine"
    assert args.identity == Path("data/testnet/identities/miner-alice.json")
    assert args.once is True
    assert args.workers == 2
    assert normalize_server_url(args.server) == "http://127.0.0.1:8000"


def test_picoin_cli_parses_testnet_cycle_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["testnet", "cycle"])

    assert args.command == "testnet"
    assert args.testnet_command == "cycle"
    assert args.miner_identity == Path("data/testnet/identities/miner-alice.json")
    assert args.validator_one_identity == Path("data/testnet/identities/validator-one.json")
    assert args.validator_two_identity == Path("data/testnet/identities/validator-two.json")
