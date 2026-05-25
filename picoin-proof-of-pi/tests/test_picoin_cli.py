import argparse
import json
from pathlib import Path

from app.core.settings import FAUCET_DEFAULT_AMOUNT
from picoin.cli import build_parser, command_node_mainnet_preflight, command_node_validation_health, normalize_server_url


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
    validation_health = parser.parse_args(["node", "validation-health", "--stale-after-seconds", "90", "--limit", "5"])
    reconcile = parser.parse_args(["node", "reconcile", "--peer", "http://peer:8000"])
    checkpoint = parser.parse_args(["node", "checkpoint", "verify", "--height", "10"])
    snapshot_export = parser.parse_args(["node", "checkpoint", "export", "--height", "10", "--output", "snap.json"])
    snapshot_import = parser.parse_args(["node", "checkpoint", "import", "--file", "snap.json", "--source", "peer"])
    snapshot_activate = parser.parse_args(["node", "checkpoint", "activate", "--snapshot-hash", "a" * 64])
    snapshot_apply = parser.parse_args(["node", "checkpoint", "apply", "--snapshot-hash", "b" * 64])
    snapshot_restore = parser.parse_args(["node", "checkpoint", "restore-peer", "--peer", "http://peer:8000", "--height", "10"])
    snapshot_restore_sqlite = parser.parse_args(
        [
            "node",
            "checkpoint",
            "restore-sqlite",
            "--file",
            "backup.sqlite3",
            "--height",
            "10",
            "--backup-current",
            "backups",
        ]
    )
    genesis_hash = parser.parse_args(["node", "genesis-hash", "--file", "genesis.json", "--mainnet"])
    wallet_create = parser.parse_args(
        ["wallet", "create", "--network", "mainnet", "--chain-id", "picoin-mainnet-v1", "--output", "wallet.json"]
    )
    compare = parser.parse_args(["node", "compare", "--peer", "http://peer:8000"])
    repair_rewards = parser.parse_args(["node", "repair-rewards"])
    mainnet_preflight = parser.parse_args(
        [
            "node",
            "mainnet-preflight",
            "--server",
            "http://mainnet:8000",
            "--peer",
            "http://peer:8000",
            "--allow-mempool",
            "--verbose",
        ]
    )
    debug_replay = parser.parse_args(
            [
                "debug",
                "--server",
                "http://127.0.0.1:8000",
                "replay-check",
                "--peer",
                "https://api.picoin.science",
            "--from-height",
            "939",
            "--to-height",
            "960",
        ]
    )

    assert peers.command == "node"
    assert peers.node_command == "peers"
    assert peers.include_stale is False
    assert sync.command == "node"
    assert sync.node_command == "sync-status"
    assert validation_health.node_command == "validation-health"
    assert validation_health.stale_after_seconds == 90
    assert validation_health.limit == 5
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
    assert snapshot_restore.checkpoint_command == "restore-peer"
    assert snapshot_restore.peer == "http://peer:8000"
    assert snapshot_restore.height == 10
    assert snapshot_restore_sqlite.checkpoint_command == "restore-sqlite"
    assert snapshot_restore_sqlite.file == Path("backup.sqlite3")
    assert snapshot_restore_sqlite.height == 10
    assert snapshot_restore_sqlite.backup_current == Path("backups")
    assert genesis_hash.node_command == "genesis-hash"
    assert genesis_hash.file == Path("genesis.json")
    assert genesis_hash.mainnet is True
    assert wallet_create.wallet_command == "create"
    assert wallet_create.network == "mainnet"
    assert wallet_create.chain_id == "picoin-mainnet-v1"
    assert wallet_create.output == Path("wallet.json")
    assert compare.node_command == "compare"
    assert compare.peer == "http://peer:8000"
    assert repair_rewards.node_command == "repair-rewards"
    assert mainnet_preflight.node_command == "mainnet-preflight"
    assert mainnet_preflight.server == "http://mainnet:8000"
    assert mainnet_preflight.peer == "http://peer:8000"
    assert mainnet_preflight.allow_mempool is True
    assert mainnet_preflight.verbose is True
    assert debug_replay.command == "debug"
    assert debug_replay.debug_command == "replay-check"
    assert debug_replay.peer == "https://api.picoin.science"
    assert debug_replay.from_height == 939
    assert debug_replay.to_height == 960


def _mainnet_preflight_payloads() -> dict[str, dict]:
    validator = {
        "eligible": True,
        "eligibility_stake": 31.416,
        "eligibility_stake_source": "wallet",
        "online_status": "online",
        "reward_address": "PI123",
        "sync_status": "synced",
        "validator_id": "validator_1",
        "wallet_stake_locked": 31.416,
    }
    return {
        "/health": {"status": "ok", "database": {"connected": True}, "chain": {"valid": True}},
        "/protocol": {
            "chain_id": "picoin-mainnet-v1",
            "faucet_enabled": False,
            "min_validator_stake": 31.416,
            "network_id": "mainnet",
            "proof_of_pi_reward_percent": 0.80,
            "protocol_version": "1.0",
            "required_validator_approvals": 3,
            "retroactive_audit_reward_percent": 0.0,
            "retroactive_audit_reward_per_audit": 0.0,
            "RETARGET_MAX_PI_POSITION": 10**15,
            "science_compute_reward_percent": 0.07,
            "scientific_development_reward_percent": 0.03,
            "scientific_development_governance_wallet": "PI6D17B68D576E0543CD5814D39F09FB58E3D35CAD355BB1",
            "scientific_development_treasury_wallet": "PIEB149E99DCD64653088B68F92D6790068428462919DD96",
            "task_expiration_seconds": 600,
            "validator_eligibility_stake_field": "wallet_stake_locked",
            "validator_eligibility_stake_source": "wallet",
            "validator_reward_percent": 0.10,
        },
        "/difficulty": {
            "RETARGET_MAX_PI_POSITION": 10**15,
            "active_task_expiration_seconds": 600,
            "effective_task_expiration_seconds": 600,
            "next_range_start": 1,
            "required_task_expiration_seconds": 600,
        },
        "/node/sync-status": {
            "chain_id": "picoin-mainnet-v1",
            "effective_latest_block_hash": "block-hash",
            "effective_latest_block_height": 10,
            "genesis_hash": "genesis-hash",
            "network_id": "mainnet",
            "pending_replay_blocks": 0,
            "replay": {
                "finalized_queue_size": 0,
                "header_queue_size": 0,
                "queue_size": 0,
            },
        },
        "/audit/full": {"valid": True, "issues": []},
        "/validators/status": {
            "eligible_validators": 3,
            "required_validator_approvals": 3,
            "validators": [dict(validator, validator_id=f"validator_{index}") for index in range(1, 4)],
        },
        "/mempool/status": {"pending_count": 0, "selected_count": 0},
        "/consensus/status": {"competing_proposal_count": 0, "fork_group_count": 0, "proposals": {"pending_missing_ancestors": 0}},
        "/validation/jobs/health": {
            "healthy": True,
            "pending_count": 0,
            "stuck_count": 0,
            "counts": {
                "assignment_timeout_pending_release": 0,
                "pending_recent": 0,
                "quorum_reached_waiting_finalization": 0,
                "stuck_no_votes": 0,
                "stuck_waiting_for_quorum": 0,
            },
        },
    }


def test_node_mainnet_preflight_passes_with_wallet_backed_validators(monkeypatch, capsys) -> None:
    payloads = _mainnet_preflight_payloads()

    def fake_get_json(server_url: str, path: str) -> dict:
        assert server_url == "http://node"
        return payloads[path]

    monkeypatch.setattr("picoin.cli.get_json", fake_get_json)
    args = argparse.Namespace(server="http://node", peer=None, allow_mempool=False, verbose=False)

    assert command_node_mainnet_preflight(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert output["network_id"] == "mainnet"
    assert output["eligible_validators"] == 3


def test_node_mainnet_preflight_fails_on_unbacked_validator_stake(monkeypatch, capsys) -> None:
    payloads = _mainnet_preflight_payloads()
    payloads["/audit/full"] = {
        "valid": False,
        "issues": [{"code": "mainnet_legacy_validator_stake", "message": "legacy stake"}],
    }
    payloads["/validators/status"]["validators"][0]["eligibility_stake_source"] = "legacy_or_wallet"

    def fake_get_json(server_url: str, path: str) -> dict:
        assert server_url == "http://node"
        return payloads[path]

    monkeypatch.setattr("picoin.cli.get_json", fake_get_json)
    args = argparse.Namespace(server="http://node", peer=None, allow_mempool=False, verbose=False)

    assert command_node_mainnet_preflight(args) == 1
    output = json.loads(capsys.readouterr().out)
    failed_checks = {check["name"] for check in output["checks"] if not check["ok"]}
    assert output["status"] == "fail"
    assert "no_legacy_mainnet_validator_stake" in failed_checks
    assert "eligible_validators_wallet_staked" in failed_checks


def test_node_mainnet_preflight_fails_on_stuck_validation_jobs(monkeypatch, capsys) -> None:
    payloads = _mainnet_preflight_payloads()
    payloads["/validation/jobs/health"] = {
        "healthy": False,
        "pending_count": 1,
        "stuck_count": 1,
        "counts": {
            "assignment_timeout_pending_release": 0,
            "pending_recent": 0,
            "quorum_reached_waiting_finalization": 0,
            "stuck_no_votes": 0,
            "stuck_waiting_for_quorum": 1,
        },
    }

    def fake_get_json(server_url: str, path: str) -> dict:
        assert server_url == "http://node"
        return payloads[path]

    monkeypatch.setattr("picoin.cli.get_json", fake_get_json)
    args = argparse.Namespace(server="http://node", peer=None, allow_mempool=False, verbose=False)

    assert command_node_mainnet_preflight(args) == 1
    output = json.loads(capsys.readouterr().out)
    failed_checks = {check["name"] for check in output["checks"] if not check["ok"]}
    assert output["status"] == "fail"
    assert "validation_jobs_not_stuck" in failed_checks


def test_node_mainnet_preflight_fails_on_noncanonical_depth_or_expiration(monkeypatch, capsys) -> None:
    payloads = _mainnet_preflight_payloads()
    payloads["/protocol"]["RETARGET_MAX_PI_POSITION"] = 1_000_000
    payloads["/difficulty"]["RETARGET_MAX_PI_POSITION"] = 1_000_000
    payloads["/difficulty"]["required_task_expiration_seconds"] = 900
    payloads["/difficulty"]["effective_task_expiration_seconds"] = 600

    def fake_get_json(server_url: str, path: str) -> dict:
        assert server_url == "http://node"
        return payloads[path]

    monkeypatch.setattr("picoin.cli.get_json", fake_get_json)
    args = argparse.Namespace(server="http://node", peer=None, allow_mempool=False, verbose=False)

    assert command_node_mainnet_preflight(args) == 1
    output = json.loads(capsys.readouterr().out)
    failed_checks = {check["name"] for check in output["checks"] if not check["ok"]}
    assert output["status"] == "fail"
    assert "pi_depth_cap_frozen" in failed_checks
    assert "dynamic_task_expiration" in failed_checks


def test_node_validation_health_command_returns_nonzero_when_unhealthy(monkeypatch, capsys) -> None:
    def fake_get_json(server_url: str, path: str) -> dict:
        assert server_url == "http://node"
        assert path == "/validation/jobs/health?stale_after_seconds=90&limit=5"
        return {"healthy": False, "pending_count": 1, "stuck_count": 1}

    monkeypatch.setattr("picoin.cli.get_json", fake_get_json)
    args = argparse.Namespace(server="http://node", stale_after_seconds=90, limit=5)

    assert command_node_validation_health(args) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["stuck_count"] == 1


def test_picoin_cli_parses_wallet_and_tx_commands() -> None:
    parser = build_parser()

    wallet = parser.parse_args(["wallet", "create", "--name", "alice", "--output", "alice.json"])
    wallet_address = parser.parse_args(["wallet", "address", "--wallet", "alice.json"])
    wallet_balance = parser.parse_args(["wallet", "balance", "--wallet", "alice.json"])
    wallet_history = parser.parse_args(["wallet", "history", "--address", "PIB", "--limit", "10"])
    wallet_send = parser.parse_args(["wallet", "send", "--wallet", "alice.json", "--to", "PIB", "--amount", "1"])
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
    assert wallet_address.wallet_command == "address"
    assert wallet_address.wallet == Path("alice.json")
    assert wallet_balance.wallet_command == "balance"
    assert wallet_balance.wallet == Path("alice.json")
    assert wallet_history.wallet_command == "history"
    assert wallet_history.limit == 10
    assert wallet_send.wallet_command == "send"
    assert wallet_send.to == "PIB"
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
    validator_stake = parser.parse_args(
        [
            "tx",
            "send",
            "--wallet",
            "alice.json",
            "--type",
            "stake",
            "--validator-id",
            "validator_123",
            "--amount",
            "31.416",
            "--nonce",
            "4",
        ]
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
    assert validator_stake.validator_id == "validator_123"
    assert validator_stake.amount == 31.416
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
