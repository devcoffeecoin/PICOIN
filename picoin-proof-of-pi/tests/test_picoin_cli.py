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
    assert args.validator_three_identity == Path("data/testnet/identities/validator-three.json")


def test_picoin_cli_parses_testnet_continuous_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["testnet", "continuous"])

    assert args.command == "testnet"
    assert args.testnet_command == "continuous"
    assert args.miners == 3
    assert args.loops == 3
    assert args.identity_dir == Path("data/testnet/identities")
    assert args.retro_audit is True


def test_picoin_cli_parses_science_create_job() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "science",
            "--address",
            "addr-lab",
            "create-job",
            "--type",
            "ai_inference",
            "--metadata-hash",
            "metadata_hash",
            "--storage-pointer",
            "ipfs://payload",
            "--reward-budget",
            "1.5",
        ]
    )

    assert args.command == "science"
    assert args.science_command == "create-job"
    assert args.address == "addr-lab"
    assert args.type == "ai_inference"
    assert args.reward_budget == 1.5


def test_picoin_cli_parses_treasury_and_reserve_commands() -> None:
    parser = build_parser()

    treasury = parser.parse_args(["treasury", "claim", "--requested-by", "gov", "--claim-to", "wallet"])
    reserve = parser.parse_args(["reserve", "status"])

    assert treasury.command == "treasury"
    assert treasury.treasury_command == "claim"
    assert treasury.requested_by == "gov"
    assert treasury.claim_to == "wallet"
    assert reserve.command == "reserve"
    assert reserve.reserve_command == "status"
