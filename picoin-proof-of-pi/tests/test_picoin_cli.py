from pathlib import Path

from app.core.settings import FAUCET_DEFAULT_AMOUNT
from picoin.cli import build_parser, normalize_server_url


def test_picoin_cli_parses_node_start_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["node", "start"])

    assert args.command == "node"
    assert args.node_command == "start"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.reload is False


def test_picoin_cli_parses_distributed_node_commands() -> None:
    parser = build_parser()

    peers = parser.parse_args(["node", "peers", "--connected-only"])
    sync = parser.parse_args(["node", "sync-status"])
    reconcile = parser.parse_args(["node", "reconcile", "--peer", "http://peer:8000"])
    checkpoint = parser.parse_args(["node", "checkpoint", "verify", "--height", "10"])
    snapshot_export = parser.parse_args(["node", "checkpoint", "export", "--height", "10", "--output", "snap.json"])
    snapshot_import = parser.parse_args(["node", "checkpoint", "import", "--file", "snap.json", "--source", "peer"])
    snapshot_activate = parser.parse_args(["node", "checkpoint", "activate", "--snapshot-hash", "a" * 64])
    snapshot_apply = parser.parse_args(["node", "checkpoint", "apply", "--snapshot-hash", "b" * 64])
    genesis_hash = parser.parse_args(["node", "genesis-hash", "--file", "genesis.json"])
    compare = parser.parse_args(["node", "compare", "--peer", "http://peer:8000"])

    assert peers.command == "node"
    assert peers.node_command == "peers"
    assert peers.include_stale is False
    assert sync.command == "node"
    assert sync.node_command == "sync-status"
    assert reconcile.node_command == "reconcile"
    assert reconcile.peer == "http://peer:8000"
    assert checkpoint.node_command == "checkpoint"
    assert checkpoint.checkpoint_command == "verify"
    assert checkpoint.height == 10
    assert snapshot_export.checkpoint_command == "export"
    assert snapshot_export.output == Path("snap.json")
    assert snapshot_import.checkpoint_command == "import"
    assert snapshot_import.file == Path("snap.json")
    assert snapshot_import.source == "peer"
    assert snapshot_activate.checkpoint_command == "activate"
    assert snapshot_activate.snapshot_hash == "a" * 64
    assert snapshot_apply.checkpoint_command == "apply"
    assert snapshot_apply.snapshot_hash == "b" * 64
    assert genesis_hash.node_command == "genesis-hash"
    assert genesis_hash.file == Path("genesis.json")
    assert compare.node_command == "compare"
    assert compare.peer == "http://peer:8000"


def test_picoin_cli_parses_wallet_and_tx_commands() -> None:
    parser = build_parser()

    wallet = parser.parse_args(["wallet", "create", "--name", "alice", "--output", "alice.json"])
    tx = parser.parse_args(
        [
            "tx",
            "send",
            "--wallet",
            "alice.json",
            "--to",
            "PIB",
            "--amount",
            "1.5",
            "--fee",
            "0.01",
        ]
    )
    nonce = parser.parse_args(["wallet", "nonce", "--address", "PIB"])
    status = parser.parse_args(["tx", "status", "--hash", "a" * 64])
    mempool = parser.parse_args(["tx", "mempool", "--status", "pending", "--limit", "25"])

    assert wallet.command == "wallet"
    assert wallet.wallet_command == "create"
    assert wallet.output == Path("alice.json")
    assert tx.command == "tx"
    assert tx.tx_command == "send"
    assert tx.wallet == Path("alice.json")
    assert tx.amount == 1.5
    assert tx.nonce is None
    assert nonce.wallet_command == "nonce"
    assert status.tx_command == "status"
    assert status.hash == "a" * 64
    assert mempool.tx_command == "mempool"
    assert mempool.limit == 25

    stake = parser.parse_args(
        ["tx", "send", "--wallet", "alice.json", "--type", "stake", "--amount", "3141.6", "--nonce", "2"]
    )
    unstake = parser.parse_args(["tx", "send", "--wallet", "alice.json", "--type", "unstake", "--nonce", "3"])
    science_job = parser.parse_args(
        [
            "tx",
            "send",
            "--wallet",
            "alice.json",
            "--type",
            "science_job_create",
            "--nonce",
            "3",
            "--payload",
            '{"job_type":"ai","metadata_hash":"meta","storage_pointer":"ipfs://job"}',
        ]
    )
    assert stake.to is None
    assert stake.amount == 3141.6
    assert unstake.amount == 0.0
    assert unstake.type == "unstake"
    assert science_job.amount == 0.0
    assert science_job.type == "science_job_create"


def test_picoin_cli_parses_consensus_commands() -> None:
    parser = build_parser()

    status = parser.parse_args(["consensus", "status"])
    propose = parser.parse_args(["consensus", "propose-block", "--block", "block.json", "--proposer", "miner-node"])
    vote = parser.parse_args(["consensus", "vote", "--proposal-id", "abc", "--identity", "validator.json"])
    votes = parser.parse_args(["consensus", "votes", "--proposal-id", "abc"])

    assert status.command == "consensus"
    assert status.consensus_command == "status"
    assert propose.block == Path("block.json")
    assert propose.proposer == "miner-node"
    assert vote.proposal_id == "abc"
    assert vote.identity == Path("validator.json")
    assert votes.consensus_command == "votes"


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

    bootstrap = parser.parse_args(["testnet", "bootstrap"])
    assert bootstrap.miner_faucet == FAUCET_DEFAULT_AMOUNT


def test_picoin_cli_parses_testnet_continuous_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["testnet", "continuous"])

    assert args.command == "testnet"
    assert args.testnet_command == "continuous"
    assert args.miners == 3
    assert args.loops == 3
    assert args.faucet == 0.0
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
            "--max-compute-units",
            "10",
            "--reward-per-unit",
            "0.25",
            "--max-reward",
            "1.5",
        ]
    )

    assert args.command == "science"
    assert args.science_command == "create-job"
    assert args.address == "addr-lab"
    assert args.type == "ai_inference"
    assert args.max_compute_units == 10
    assert args.reward_per_unit == 0.25
    assert args.max_reward == 1.5


def test_picoin_cli_parses_treasury_and_reserve_commands() -> None:
    parser = build_parser()

    treasury = parser.parse_args(["treasury", "claim", "--requested-by", "gov", "--claim-to", "wallet"])
    reserve = parser.parse_args(["reserve", "pause", "--signer", "signer-1"])

    assert treasury.command == "treasury"
    assert treasury.treasury_command == "claim"
    assert treasury.requested_by == "gov"
    assert treasury.claim_to == "wallet"
    assert reserve.command == "reserve"
    assert reserve.reserve_command == "pause"
    assert reserve.signer == "signer-1"
