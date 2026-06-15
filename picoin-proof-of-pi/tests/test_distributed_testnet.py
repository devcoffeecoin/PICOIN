import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.crypto import canonical_json, hash_result, sha256_text
from app.core.money import to_units
from app.core.pi import calculate_pi_segment
from app.core.settings import (
    CHAIN_ID,
    GENESIS_HASH,
    MEMPOOL_TX_TTL_SECONDS,
    NETWORK_ID,
    PROTOCOL_VERSION,
    VALIDATION_MODE,
)
from app.core.signatures import (
    build_submission_signature_payload,
    build_validation_result_signature_payload,
    generate_keypair,
    sign_payload,
)
from app.db.database import get_connection, init_db
from app.models.schemas import SignedTransactionRequest
from app.services.consensus import (
    _mark_replay_divergent,
    apply_orphan_reorg,
    block_hash_debug,
    debug_block_determinism,
    get_replay_status,
    list_orphan_candidates,
    plan_orphan_reorg,
    prepare_orphan_reorg,
    propose_block,
    replay_finalized_blocks,
)
from app.services.mining import (
    create_next_task,
    get_balance_amount,
    get_block,
    get_full_economic_audit,
    get_health_status,
    register_miner,
    register_validator,
    repair_missing_block_rewards,
    submit_task,
    verify_chain,
)
from app.services.network import (
    NetworkError,
    get_blocks_since,
    get_transaction,
    get_sync_status,
    gossip_json,
    heartbeat_peer,
    list_mempool,
    list_peers,
    discover_peers,
    receive_block_header,
    reconcile_connected_peers,
    reconcile_peer,
    register_peer,
    select_reconcile_peers,
    submit_transaction,
    sync_blocks_until,
)
from app.services.science import get_science_account, get_science_job, get_science_reserve_governance
from app.services.state import (
    activate_imported_snapshot,
    apply_imported_snapshot_state,
    calculate_state_root,
    create_canonical_checkpoint,
    export_canonical_snapshot,
    import_canonical_snapshot,
    latest_checkpoint,
    list_imported_snapshots,
    restore_imported_snapshot_state,
    update_block_state_root,
    validate_snapshot_document,
    verify_checkpoint,
)
from app.services.genesis import genesis_allocations_hash
from app.services.treasury import (
    SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID,
    get_scientific_development_treasury,
)
from app.services.transactions import (
    apply_block_transactions,
    get_wallet_nonce_status,
    select_block_transactions,
    transaction_commitment,
)
from app.services.wallet import (
    address_from_public_key,
    address_matches_public_key,
    create_wallet,
    is_valid_address,
    legacy_address_from_public_key,
    sign_transaction,
)
from picoin.cli import _snapshot_from_sqlite


def _init_network_db(tmp_path, monkeypatch, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)


def _full_block_with_nonlocal_parent(tmp_path, monkeypatch, *, source_name: str, target_name: str) -> dict:
    _init_network_db(tmp_path, monkeypatch, source_name)
    miner_key = generate_keypair()
    miner = register_miner("missing-parent-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    block = dict(get_block(1) or {})
    assert block
    block["previous_hash"] = "f" * 64
    block["block_hash"] = block_hash_debug(block)["computed_hash"]

    _init_network_db(tmp_path, monkeypatch, target_name)
    return block


def _certified_reorg_block(
    *,
    height: int,
    previous_hash: str,
    task_id: str,
    job_id: str,
    miner_id: str = "remote-reorg-miner",
    timestamp: str = "2026-06-09T17:30:00+00:00",
    transactions: list[dict] | None = None,
) -> dict:
    samples = [{"position": height, "digit": str(height % 10), "proof": []}]
    transactions = transactions or []
    tx_commitment = transaction_commitment(transactions)
    block = {
        "height": height,
        "previous_hash": previous_hash,
        "miner_id": miner_id,
        "range_start": height,
        "range_end": height,
        "algorithm": "bbp_hex_v1",
        "result_hash": sha256_text(f"remote-result:{height}:{task_id}"),
        "merkle_root": sha256_text(f"remote-merkle:{height}:{task_id}"),
        "samples": samples,
        "timestamp": timestamp,
        "reward": 2.51328,
        "protocol_version": PROTOCOL_VERSION,
        "validation_mode": VALIDATION_MODE,
        "total_block_ms": 1000,
        "tx_merkle_root": tx_commitment["tx_merkle_root"] if transactions else "",
        "tx_count": tx_commitment["tx_count"],
        "tx_hashes": tx_commitment["tx_hashes"],
        "fee_reward": tx_commitment["fee_reward"],
        "transactions": transactions,
        "task_id": task_id,
    }
    block["block_hash"] = block_hash_debug({**block, "block_hash": "0" * 64})["computed_hash"]
    block["finality_certificate"] = _finality_certificate_for_test_block(block, task_id=task_id, job_id=job_id)
    return block


def _finality_certificate_for_test_block(block: dict, *, task_id: str, job_id: str) -> dict:
    created_at = block["timestamp"]
    votes = []
    for index in range(3):
        validator_key = generate_keypair()
        validator_id = f"validator_reorg_{index}"
        signed_at = f"2026-06-09T17:30:0{index}+00:00"
        reason = "external validator accepted samples"
        signature_payload = build_validation_result_signature_payload(
            job_id=job_id,
            validator_id=validator_id,
            task_id=task_id,
            approved=True,
            reason=reason,
            signed_at=signed_at,
        )
        votes.append(
            {
                "validator_id": validator_id,
                "validator_name": validator_id,
                "validator_public_key": validator_key["public_key"],
                "validator_reward_address": None,
                "approved": True,
                "reason": reason,
                "signature": sign_payload(validator_key["private_key"], signature_payload),
                "signed_at": signed_at,
                "signature_payload": signature_payload,
                "validation_ms": 1,
                "submit_result_latency_ms": 1,
                "voted_at": signed_at,
            }
        )
    payload = {
        "version": "picoin-finality-v1",
        "network_id": NETWORK_ID,
        "chain_id": CHAIN_ID,
        "protocol_version": block.get("protocol_version") or PROTOCOL_VERSION,
        "protocol_params_id": block.get("protocol_params_id"),
        "block": {
            "height": int(block["height"]),
            "block_hash": block["block_hash"],
            "previous_hash": block["previous_hash"],
            "state_root": block.get("state_root"),
            "miner_id": block["miner_id"],
            "task_id": task_id,
            "result_hash": block["result_hash"],
            "merkle_root": block.get("merkle_root"),
            "tx_merkle_root": block.get("tx_merkle_root"),
            "tx_count": int(block.get("tx_count") or 0),
            "tx_fee_total_units": int(transaction_commitment(block.get("transactions") or [])["tx_fee_total_units"]),
            "selected_tx_hashes_hash": None,
        },
        "validation": {
            "job_id": job_id,
            "challenge_seed": sha256_text(job_id),
            "sample_count": len(block.get("samples") or []),
            "required_approvals": 3,
            "approval_count": 3,
            "status": "approved",
        },
    }
    return {
        "block_height": int(block["height"]),
        "block_hash": block["block_hash"],
        "task_id": task_id,
        "job_id": job_id,
        "miner_id": block["miner_id"],
        "network_id": NETWORK_ID,
        "chain_id": str(CHAIN_ID),
        "protocol_version": payload["protocol_version"],
        "protocol_params_id": payload.get("protocol_params_id"),
        "required_approvals": 3,
        "approval_count": 3,
        "certificate_hash": sha256_text(canonical_json({"payload": payload, "votes": votes})),
        "payload": payload,
        "votes": votes,
        "created_at": created_at,
    }


def _queue_reorg_block_proposal(block: dict) -> None:
    timestamp = block["timestamp"]
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO consensus_block_proposals (
                proposal_id, block_hash, height, previous_hash, proposer_node_id,
                status, payload, approvals, rejections, rejection_reason,
                finalized_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'remote-node', 'pending_missing_ancestors', ?, 0, 0,
                    'proposal accepted but previous_hash is not local chain tip', NULL, ?, ?)
            """,
            (
                sha256_text(f"proposal:{block['block_hash']}"),
                block["block_hash"],
                block["height"],
                block["previous_hash"],
                json.dumps(block, sort_keys=True),
                timestamp,
                timestamp,
            ),
        )


def _queue_replay_backlog_for_divergence_guard() -> None:
    receive_block_header(
        {
            "height": 1,
            "previous_hash": "f" * 64,
            "block_hash": "a" * 64,
            "timestamp": "2026-05-12T00:00:00+00:00",
        },
        source_peer_id="peer-a",
    )
    status = get_replay_status()
    assert status["queue_size"] == 1


def _retarget_active_protocol_for_test(*, segment_size: int = 8, difficulty: float = 0.03125) -> int:
    with get_connection() as connection:
        current = connection.execute(
            "SELECT * FROM protocol_params WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert current is not None
        created_at = "2026-05-12T00:00:00+00:00"
        connection.execute("UPDATE protocol_params SET active = 0 WHERE active = 1")
        cursor = connection.execute(
            """
            INSERT INTO protocol_params (
                protocol_version, algorithm, validation_mode, required_validator_approvals,
                range_assignment_mode, max_pi_position, range_assignment_max_attempts,
                segment_size, sample_count, task_expiration_seconds, max_active_tasks_per_miner,
                base_reward, difficulty, RETARGET_MAX_PI_POSITION, target_block_time_ms,
                retarget_reason, retarget_source_window, retarget_source_details,
                previous_protocol_params_id, active, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                current["protocol_version"],
                current["algorithm"],
                current["validation_mode"],
                current["required_validator_approvals"],
                current["range_assignment_mode"],
                current["max_pi_position"],
                current["range_assignment_max_attempts"],
                segment_size,
                current["sample_count"],
                current["task_expiration_seconds"],
                current["max_active_tasks_per_miner"],
                current["base_reward"],
                difficulty,
                current["RETARGET_MAX_PI_POSITION"],
                current["target_block_time_ms"],
                "test retarget",
                20,
                '{"test":true}',
                current["id"],
                created_at,
            ),
        )
        new_params_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO retarget_events (
                previous_protocol_params_id, new_protocol_params_id, epoch_start_height,
                epoch_end_height, epoch_block_count, average_block_ms, target_block_ms,
                old_difficulty, new_difficulty, adjustment_factor, action, reason, created_at
            )
            VALUES (?, ?, 1, 20, 20, 150000.0, ?, ?, ?, 0.5, 'adjust', 'test retarget', ?)
            """,
            (
                current["id"],
                new_params_id,
                current["target_block_time_ms"],
                current["difficulty"],
                difficulty,
                created_at,
            ),
        )
        return new_params_id


def test_canonical_json_serialization_is_stable() -> None:
    payload = {"z": 1, "a": {"b": 2, "a": 1}, "list": [{"y": 2, "x": 1}]}

    encoded = canonical_json(payload)

    assert encoded == '{"a":{"a":1,"b":2},"list":[{"x":1,"y":2}],"z":1}'
    assert " " not in encoded
    assert canonical_json(json.loads(encoded)) == encoded


def test_wallet_address_derivation_uses_stable_checksum() -> None:
    keypair = generate_keypair()

    first = address_from_public_key(keypair["public_key"])
    second = address_from_public_key(keypair["public_key"])
    legacy = legacy_address_from_public_key(keypair["public_key"])
    mutated = f"{first[:-1]}{'0' if first[-1] != '0' else '1'}"

    assert first == second
    assert first.startswith("PI")
    assert is_valid_address(first) is True
    assert is_valid_address(legacy) is True
    assert address_matches_public_key(first, keypair["public_key"]) is True
    assert address_matches_public_key(legacy, keypair["public_key"]) is True
    assert is_valid_address(mutated) is False
    assert address_matches_public_key(mutated, keypair["public_key"]) is False
    assert is_valid_address("PI340F7EEA37754C5F9C9ADE84D98F9B4AE10F0E") is True


def test_block_hash_debug_accepts_historical_fraud_field_schema(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "hash-schema-fraud-fields.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("hash-schema-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    block = get_blocks_since(0)["blocks"][0]
    block.pop("fraudulent", None)
    block.pop("fraud_reason", None)
    block.pop("fraud_detected_at", None)

    debug = block_hash_debug(block)

    assert debug["matched"] is True
    assert debug["matched_variant"] is not None


def test_get_blocks_since_uses_consensus_vote_id_order_for_validator_rewards(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "block-sync-consensus-votes.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("block-sync-consensus-votes-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    timestamp = "2026-05-12T00:00:00+00:00"

    with get_connection() as connection:
        block_hash = get_block(1)["block_hash"]
        proposal_row = connection.execute(
            "SELECT proposal_id FROM consensus_block_proposals WHERE block_hash = ?",
            (block_hash,),
        ).fetchone()
        proposal_id = proposal_row["proposal_id"]
        connection.execute(
            """
            INSERT INTO consensus_votes (
                vote_id, proposal_id, block_hash, validator_id, approved, reason,
                signature, signed_at, created_at
            )
            VALUES (?, ?, ?, ?, 1, 'approved', 'signature-a', ?, ?)
            """,
            ("vote_a", proposal_id, block_hash, "validator_a", timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, amount_units, balance_after,
                balance_after_units, entry_type, block_height, related_id, description, created_at
            )
            VALUES (?, 'validator', 0.1, 10000000, 0.1, 10000000, 'validator_reward', 1, ?, 'test reward', ?)
            """,
            ("validator_a", proposal_id, timestamp),
        )

    block = get_blocks_since(0)["blocks"][0]

    assert block["validator_reward"]["validator_ids"] == ["validator_a"]


def test_peer_registry_and_heartbeat(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peers.sqlite3")

    peer = register_peer(
        node_id="validator-1",
        peer_address="http://validator-1:8000",
        peer_type="validator",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    heartbeat = heartbeat_peer(peer["peer_id"])
    peers = list_peers()

    assert peer["status"] == "connected"
    assert heartbeat["peer_id"] == peer["peer_id"]
    assert peers[0]["peer_type"] == "validator"


def test_peer_discovery_registers_seed_and_one_hop_peer(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peer-discovery.sqlite3")

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=0):
        if url == "http://seed:8000/node/identity":
            return Response(
                {
                    "node_id": "seed-node",
                    "peer_address": "http://seed:8000",
                    "peer_type": "bootstrap",
                    "protocol_version": PROTOCOL_VERSION,
                    "network_id": NETWORK_ID,
                    "chain_id": CHAIN_ID,
                    "genesis_hash": GENESIS_HASH,
                    "bootstrap_peers": [],
                }
            )
        if url == "http://seed:8000/node/peers":
            return Response(
                [
                    {
                        "node_id": "validator-node",
                        "peer_address": "http://validator:8000",
                        "peer_type": "validator",
                        "protocol_version": PROTOCOL_VERSION,
                        "network_id": NETWORK_ID,
                        "chain_id": CHAIN_ID,
                        "genesis_hash": GENESIS_HASH,
                        "connected_at": "2026-05-21T00:00:00+00:00",
                        "last_seen": "2026-05-21T00:00:00+00:00",
                        "status": "connected",
                        "metadata": {},
                    }
                ]
            )
        raise AssertionError(url)

    monkeypatch.setattr("app.services.network.requests.get", fake_get)

    result = discover_peers(["http://seed:8000"])
    peers = list_peers()

    assert result["status"] == "ok"
    assert result["registered"] == 2
    assert {peer["peer_address"] for peer in peers} == {"http://seed:8000", "http://validator:8000"}


def test_register_peer_normalizes_duplicate_scheme(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peer-normalize.sqlite3")

    peer = register_peer(
        node_id="validator-1",
        peer_address="http://http://validator-1:8000",
        peer_type="validator",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )

    assert peer["peer_address"] == "http://validator-1:8000"
    assert peer["status"] == "connected"


def test_select_reconcile_peers_filters_local_stale_and_wrong_identity(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peer-select.sqlite3")

    register_peer(
        node_id="local-node-copy",
        peer_address="http://127.0.0.1:8000",
        peer_type="full",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    healthy_a = register_peer(
        node_id="peer-a",
        peer_address="http://peer-a:8000",
        peer_type="full",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    healthy_b = register_peer(
        node_id="peer-b",
        peer_address="http://peer-b:8000",
        peer_type="bootstrap",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    stale = register_peer(
        node_id="peer-stale",
        peer_address="http://peer-stale:8000",
        peer_type="full",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    wrong_identity = register_peer(
        node_id="peer-wrong",
        peer_address="http://peer-wrong:8000",
        peer_type="full",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    with get_connection() as connection:
        connection.execute("UPDATE network_peers SET status = 'stale' WHERE peer_id = ?", (stale["peer_id"],))
        connection.execute("UPDATE network_peers SET network_id = ? WHERE peer_id = ?", ("wrong-network", wrong_identity["peer_id"]))

    selected = select_reconcile_peers(limit=10)

    assert {peer["peer_id"] for peer in selected} == {healthy_a["peer_id"], healthy_b["peer_id"]}
    assert {peer["peer_address"] for peer in selected} == {"http://peer-a:8000", "http://peer-b:8000"}


def test_select_reconcile_peers_prefers_public_bootstraps_over_noisy_validators(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peer-select-public-bootstrap.sqlite3")
    monkeypatch.setattr("app.services.network.BOOTSTRAP_PEERS", ["http://api.picoin.science"], raising=False)

    candidate_a = register_peer(
        node_id="mainnet-bootstrap-candidate-a",
        peer_address="http://178.62.30.17:8000",
        peer_type="bootstrap",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    candidate_b = register_peer(
        node_id="mainnet-bootstrap-candidate-b",
        peer_address="http://138.68.139.141:8000",
        peer_type="bootstrap",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    seed_bootstrap = register_peer(
        node_id="bootstrap",
        peer_address="http://api.picoin.science",
        peer_type="bootstrap",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    public_full = register_peer(
        node_id="public-full-node",
        peer_address="http://203.0.113.10:8000",
        peer_type="full",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    register_peer(
        node_id="validator-loopback",
        peer_address="http://127.0.0.1:8131",
        peer_type="validator",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    register_peer(
        node_id="validator-placeholder",
        peer_address="http://ваш-ip:8000",
        peer_type="validator",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    noisy_validator = register_peer(
        node_id="validator-noisy",
        peer_address="http://165.227.181.138:8000",
        peer_type="validator",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )

    selected = select_reconcile_peers(limit=5)
    selected_addresses = [peer["peer_address"] for peer in selected]

    assert set(selected_addresses[:2]) == {candidate_a["peer_address"], candidate_b["peer_address"]}
    assert seed_bootstrap["peer_address"] in selected_addresses[:3]
    assert selected_addresses.index(public_full["peer_address"]) < selected_addresses.index(noisy_validator["peer_address"])
    assert "http://127.0.0.1:8131" not in selected_addresses
    assert "http://ваш-ip:8000" not in selected_addresses


def test_reconcile_connected_peers_attempts_multiple_selected_peers(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peer-reconcile-multiple.sqlite3")

    register_peer(
        node_id="peer-a",
        peer_address="http://peer-a:8000",
        peer_type="full",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )
    register_peer(
        node_id="peer-b",
        peer_address="http://peer-b:8000",
        peer_type="bootstrap",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )

    attempted: list[str] = []

    def fake_reconcile_peer(peer_address: str) -> dict:
        attempted.append(peer_address)
        return {
            "peer_address": peer_address,
            "peers_seen": 0,
            "transactions_imported": 0,
            "proposals_imported": 0,
            "blocks_imported": 0,
            "errors": [],
        }

    monkeypatch.setattr("app.services.network.reconcile_peer", fake_reconcile_peer)

    result = reconcile_connected_peers(limit=10)

    assert set(attempted) == {"http://peer-a:8000", "http://peer-b:8000"}
    assert result["attempted"] == 2
    assert {peer["peer_address"] for peer in result["selected_peers"]} == set(attempted)
    assert result["errors"] == 0


def test_reconcile_peer_uses_mempool_inventory_for_missing_transactions(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peer-reconcile-inventory.sqlite3")
    tx_hash = "a" * 64
    requested_urls: list[str] = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=0):
        requested_urls.append(url)
        if url == "http://peer-a:8000/node/identity":
            return Response(
                {
                    "node_id": "peer-a",
                    "peer_address": "http://peer-a:8000",
                    "peer_type": "full",
                    "protocol_version": PROTOCOL_VERSION,
                    "network_id": NETWORK_ID,
                    "chain_id": CHAIN_ID,
                    "genesis_hash": GENESIS_HASH,
                    "bootstrap_peers": [],
                }
            )
        if url == "http://peer-a:8000/node/peers":
            return Response([])
        if url == "http://peer-a:8000/mempool/inventory?status=pending&limit=100":
            return Response(
                {
                    "status": "pending",
                    "count": 1,
                    "transactions": [
                        {
                            "tx_hash": tx_hash,
                            "status": "pending",
                            "sender": "sender-a",
                            "nonce": 1,
                            "fee_units": 10,
                        }
                    ],
                }
            )
        if url == f"http://peer-a:8000/tx/{tx_hash}":
            return Response({"tx_hash": tx_hash, "status": "pending"})
        if url == "http://peer-a:8000/validators/heartbeat/inventory?limit=100":
            return Response({"heartbeats": []})
        if url == "http://peer-a:8000/node/sync/blocks?from_height=0&limit=100":
            return Response({"from_height": 0, "count": 0, "blocks": []})
        if url == "http://peer-a:8000/consensus/proposals?limit=100":
            return Response([])
        raise AssertionError(url)

    imported: list[dict] = []

    def fake_submit_transaction(tx, propagated=False):
        imported.append({"tx": tx, "propagated": propagated})
        return tx

    monkeypatch.setattr("app.services.network.requests.get", fake_get)
    monkeypatch.setattr("app.services.network.submit_transaction", fake_submit_transaction)

    result = reconcile_peer("http://peer-a:8000")

    assert result["mempool_inventory_seen"] == 1
    assert result["mempool_inventory_missing"] == 1
    assert result["transactions_seen"] == 1
    assert result["transactions_imported"] == 1
    assert imported == [{"tx": {"tx_hash": tx_hash, "status": "pending"}, "propagated": True}]
    assert "http://peer-a:8000/mempool?limit=100" not in requested_urls


def test_reconcile_peer_imports_validator_heartbeat_inventory(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peer-reconcile-validator-heartbeats.sqlite3")
    keys = generate_keypair()
    heartbeat = {
        "validator_id": "validator_peer_gossip",
        "node_id": "validator-peer-node",
        "public_key": keys["public_key"],
        "address": "http://validator-peer:8000",
        "local_height": 100,
        "effective_height": 100,
        "latest_block_hash": "a" * 64,
        "pending_replay_blocks": 0,
        "sync_lag": 0,
        "version": PROTOCOL_VERSION,
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }
    heartbeat["signature"] = sign_payload(keys["private_key"], heartbeat)

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=0):
        if url == "http://peer-a:8000/node/identity":
            return Response(
                {
                    "node_id": "peer-a",
                    "peer_address": "http://peer-a:8000",
                    "peer_type": "full",
                    "protocol_version": PROTOCOL_VERSION,
                    "network_id": NETWORK_ID,
                    "chain_id": CHAIN_ID,
                    "genesis_hash": GENESIS_HASH,
                    "bootstrap_peers": [],
                }
            )
        if url == "http://peer-a:8000/node/peers":
            return Response([])
        if url == "http://peer-a:8000/mempool/inventory?status=pending&limit=100":
            return Response({"transactions": []})
        if url == "http://peer-a:8000/validators/heartbeat/inventory?limit=100":
            return Response({"heartbeats": [{"heartbeat": heartbeat, "observed_at": heartbeat["heartbeat_at"]}]})
        if url == "http://peer-a:8000/node/sync/blocks?from_height=0&limit=100":
            return Response({"from_height": 0, "count": 0, "blocks": []})
        if url == "http://peer-a:8000/consensus/proposals?limit=100":
            return Response([])
        raise AssertionError(url)

    monkeypatch.setattr("app.services.network.requests.get", fake_get)

    result = reconcile_peer("http://peer-a:8000")

    assert result["validator_heartbeat_inventory_seen"] == 1
    assert result["validator_heartbeats_imported"] == 1
    with get_connection() as connection:
        validator = connection.execute(
            "SELECT online_status, node_id, advertised_address FROM validators WHERE validator_id = ?",
            ("validator_peer_gossip",),
        ).fetchone()
    assert validator["online_status"] == "online"
    assert validator["node_id"] == "validator-peer-node"
    assert validator["advertised_address"] == "http://validator-peer:8000"


def test_reconcile_peer_defers_votes_for_missing_validation_jobs(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peer-reconcile-missing-vote-jobs.sqlite3")
    vote_rows = [
        {
            "vote": {
                "job_id": "job_missing_a",
                "task_id": "task_missing_a",
                "validator_id": "validator_peer_a",
                "approved": True,
            }
        },
        {
            "vote": {
                "job_id": "job_missing_b",
                "task_id": "task_missing_b",
                "validator_id": "validator_peer_b",
                "approved": True,
            }
        },
    ]

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=0):
        if url == "http://peer-a:8000/node/identity":
            return Response(
                {
                    "node_id": "peer-a",
                    "peer_address": "http://peer-a:8000",
                    "peer_type": "full",
                    "protocol_version": PROTOCOL_VERSION,
                    "network_id": NETWORK_ID,
                    "chain_id": CHAIN_ID,
                    "genesis_hash": GENESIS_HASH,
                    "bootstrap_peers": [],
                }
            )
        if url == "http://peer-a:8000/node/peers":
            return Response([])
        if url == "http://peer-a:8000/mempool/inventory?status=pending&limit=100":
            return Response({"transactions": []})
        if url == "http://peer-a:8000/validators/heartbeat/inventory?limit=100":
            return Response({"heartbeats": []})
        if url == "http://peer-a:8000/tasks/inventory?limit=100":
            return Response({"tasks": []})
        if url == "http://peer-a:8000/validation/jobs/inventory?status=pending&limit=100":
            return Response({"jobs": []})
        if url == "http://peer-a:8000/validation/votes/inventory?limit=100":
            return Response({"votes": vote_rows})
        if url == "http://peer-a:8000/node/sync/blocks?from_height=0&limit=100":
            return Response({"from_height": 0, "count": 0, "blocks": []})
        if url == "http://peer-a:8000/consensus/proposals?limit=100":
            return Response([])
        raise AssertionError(url)

    def fail_vote_import(*args, **kwargs):
        raise AssertionError("votes without local jobs should be deferred")

    monkeypatch.setattr("app.services.network.requests.get", fake_get)
    monkeypatch.setattr("app.services.mining.receive_validation_vote_gossip", fail_vote_import)

    result = reconcile_peer("http://peer-a:8000")

    assert result["validation_vote_inventory_seen"] == 2
    assert result["validation_vote_inventory_missing"] == 0
    assert result["validation_vote_inventory_missing_job"] == 2
    assert result["validation_votes_imported"] == 0
    assert result["errors"] == []


def test_reconcile_peer_skips_old_proposals_when_block_sync_times_out(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peer-reconcile-skip-old-proposals.sqlite3")
    miner_key = generate_keypair()
    miner = register_miner("local-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=0):
        if url == "http://peer-a:8000/node/identity":
            return Response(
                {
                    "node_id": "peer-a",
                    "peer_address": "http://peer-a:8000",
                    "peer_type": "full",
                    "protocol_version": PROTOCOL_VERSION,
                    "network_id": NETWORK_ID,
                    "chain_id": CHAIN_ID,
                    "genesis_hash": GENESIS_HASH,
                    "bootstrap_peers": [],
                }
            )
        if url == "http://peer-a:8000/node/peers":
            return Response([])
        if url == "http://peer-a:8000/mempool/inventory?status=pending&limit=100":
            return Response({"transactions": []})
        if url == "http://peer-a:8000/validators/heartbeat/inventory?limit=100":
            return Response({"heartbeats": []})
        if url == "http://peer-a:8000/tasks/inventory?limit=100":
            return Response({"tasks": []})
        if url == "http://peer-a:8000/validation/jobs/inventory?status=pending&limit=100":
            return Response({"jobs": []})
        if url == "http://peer-a:8000/validation/votes/inventory?limit=100":
            return Response({"votes": []})
        if url == "http://peer-a:8000/node/sync/blocks?from_height=1&limit=100":
            raise TimeoutError("slow block inventory")
        if url == "http://peer-a:8000/consensus/proposals?limit=100":
            return Response(
                [
                    {
                        "proposal_id": "old-local-height-proposal",
                        "proposer_node_id": "peer-a",
                        "payload": {
                            "height": 1,
                            "previous_hash": GENESIS_HASH,
                            "block_hash": "a" * 64,
                            "timestamp": "2026-05-12T00:00:00+00:00",
                        },
                    }
                ]
            )
        raise AssertionError(url)

    def fail_propose_block(*args, **kwargs):
        raise AssertionError("already-finalized proposals should be skipped")

    monkeypatch.setattr("app.services.network.requests.get", fake_get)
    monkeypatch.setattr("app.services.consensus.propose_block", fail_propose_block)

    result = reconcile_peer("http://peer-a:8000")

    assert result["proposals_seen"] == 1
    assert result["proposals_imported"] == 0
    assert result["errors"] == ["blocks: slow block inventory"]


def test_reconcile_peer_fallback_imports_pending_mempool_only(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peer-reconcile-fallback-pending.sqlite3")
    pending_hash = "b" * 64
    confirmed_hash = "c" * 64
    requested_urls: list[str] = []

    class Response:
        def __init__(self, payload, *, fail: bool = False):
            self._payload = payload
            self._fail = fail

        def raise_for_status(self):
            if self._fail:
                raise RuntimeError("inventory unavailable")

        def json(self):
            return self._payload

    def fake_get(url, timeout=0):
        requested_urls.append(url)
        if url == "http://peer-a:8000/node/identity":
            return Response(
                {
                    "node_id": "peer-a",
                    "peer_address": "http://peer-a:8000",
                    "peer_type": "full",
                    "protocol_version": PROTOCOL_VERSION,
                    "network_id": NETWORK_ID,
                    "chain_id": CHAIN_ID,
                    "genesis_hash": GENESIS_HASH,
                    "bootstrap_peers": [],
                }
            )
        if url == "http://peer-a:8000/node/peers":
            return Response([])
        if url == "http://peer-a:8000/mempool/inventory?status=pending&limit=100":
            return Response({}, fail=True)
        if url == "http://peer-a:8000/mempool?status=pending&limit=100":
            return Response(
                [
                    {"tx_hash": pending_hash, "status": "pending"},
                    {"tx_hash": confirmed_hash, "status": "confirmed"},
                ]
            )
        if url == "http://peer-a:8000/validators/heartbeat/inventory?limit=100":
            return Response({"heartbeats": []})
        if url == "http://peer-a:8000/node/sync/blocks?from_height=0&limit=100":
            return Response({"from_height": 0, "count": 0, "blocks": []})
        if url == "http://peer-a:8000/consensus/proposals?limit=100":
            return Response([])
        raise AssertionError(url)

    imported: list[dict] = []

    def fake_submit_transaction(tx, propagated=False):
        imported.append({"tx": tx, "propagated": propagated})
        return tx

    monkeypatch.setattr("app.services.network.requests.get", fake_get)
    monkeypatch.setattr("app.services.network.submit_transaction", fake_submit_transaction)

    result = reconcile_peer("http://peer-a:8000")

    assert result["mempool_fallback_seen"] == 2
    assert result["mempool_fallback_pending"] == 1
    assert result["transactions_seen"] == 1
    assert result["transactions_imported"] == 1
    assert imported == [{"tx": {"tx_hash": pending_hash, "status": "pending"}, "propagated": True}]
    assert "http://peer-a:8000/mempool?status=pending&limit=100" in requested_urls
    assert "http://peer-a:8000/mempool?limit=100" not in requested_urls


def test_discover_peers_skips_invalid_discovered_peer_address(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "peer-discovery-invalid.sqlite3")
    monkeypatch.setattr("app.services.network.BOOTSTRAP_PEERS", [], raising=False)

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=0):
        if url == "http://seed:8000/node/identity":
            return Response(
                {
                    "node_id": "seed-node",
                    "peer_address": "http://seed:8000",
                    "peer_type": "bootstrap",
                    "protocol_version": PROTOCOL_VERSION,
                    "network_id": NETWORK_ID,
                    "chain_id": CHAIN_ID,
                    "genesis_hash": GENESIS_HASH,
                    "bootstrap_peers": [],
                }
            )
        if url == "http://seed:8000/node/peers":
            return Response(
                [
                    {
                        "node_id": "validator-node",
                        "peer_address": "http://http://validator:8000",
                        "peer_type": "validator",
                        "protocol_version": PROTOCOL_VERSION,
                        "network_id": NETWORK_ID,
                        "chain_id": CHAIN_ID,
                        "genesis_hash": GENESIS_HASH,
                        "connected_at": "2026-05-21T00:00:00+00:00",
                        "last_seen": "2026-05-21T00:00:00+00:00",
                        "status": "connected",
                        "metadata": {},
                    }
                ]
            )
        raise AssertionError(url)

    monkeypatch.setattr("app.services.network.requests.get", fake_get)

    result = discover_peers(["http://seed:8000"])
    peers = list_peers()

    assert result["registered"] == 2
    assert result["status"] == "ok"
    assert not result["errors"]
    assert {peer["peer_address"] for peer in peers} == {"http://seed:8000", "http://validator:8000"}


def test_gossip_json_skips_invalid_stored_peer_address(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "gossip-invalid-peer.sqlite3")

    peer = register_peer(
        node_id="validator-1",
        peer_address="http://validator-1:8000",
        peer_type="validator",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )

    with get_connection() as connection:
        connection.execute(
            "UPDATE network_peers SET peer_address = ? WHERE peer_id = ?",
            ("http://", peer["peer_id"]),
        )

    def fake_post(url, json=None, timeout=0):
        raise AssertionError("requests.post should not be called for invalid peer address")

    monkeypatch.setattr("app.services.network.requests.post", fake_post)

    result = gossip_json("/tx/receive", {"dummy": "payload"}, "tx_gossip")

    assert result["attempted"] == 1
    assert result["succeeded"] == 0
    assert result["failed"] == 1
    assert result["peers"][0]["error"] == "invalid peer_address"


def test_submit_transaction_marks_propagated_after_gossip_success(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "submit-gossip.sqlite3")
    monkeypatch.setattr("app.services.network._validate_signed_transaction", lambda tx: None)

    register_peer(
        node_id="validator-1",
        peer_address="http://validator-1:8000",
        peer_type="validator",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )

    class Response:
        status_code = 200

    def fake_post(url, json=None, timeout=0):
        return Response()

    monkeypatch.setattr("app.services.network.requests.post", fake_post)

    tx = {
        "tx_hash": "a" * 64,
        "tx_type": "transfer",
        "sender": "PI00000000000000000000000000000000000000",
        "recipient": "PI11111111111111111111111111111111111111",
        "amount": 0,
        "amount_units": 0,
        "nonce": 1,
        "fee": 1,
        "fee_units": 1,
        "payload": {},
        "public_key": "pubkey",
        "signature": "signature",
        "timestamp": "2026-05-22T14:00:00+00:00",
        "network_id": NETWORK_ID,
        "chain_id": CHAIN_ID,
    }

    submit_transaction(tx)

    stored = get_transaction(tx["tx_hash"])
    assert stored["propagated"] is True


def test_transaction_gossip_strips_private_and_extra_fields(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "submit-gossip-public-only.sqlite3")

    register_peer(
        node_id="validator-1",
        peer_address="http://validator-1:8000",
        peer_type="validator",
        protocol_version=PROTOCOL_VERSION,
        network_id=NETWORK_ID,
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )

    posted_payloads: list[dict] = []

    class Response:
        status_code = 201

    def fake_post(url, json=None, timeout=0):
        posted_payloads.append(dict(json or {}))
        return Response()

    monkeypatch.setattr("app.services.network.requests.post", fake_post)

    wallet = create_wallet("alice")
    recipient = create_wallet("bob")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=recipient["address"],
        amount=1,
        nonce=1,
    )
    tx["private_key"] = wallet["private_key"]
    tx["local_only_note"] = "must not leave this node"

    submit_transaction(tx)

    assert len(posted_payloads) == 1
    outbound = posted_payloads[0]
    assert "private_key" not in outbound
    assert "local_only_note" not in outbound
    assert outbound["tx_hash"] == tx["tx_hash"]
    assert outbound["public_key"] == tx["public_key"]
    assert outbound["signature"] == tx["signature"]
    assert outbound["payload"] == tx["payload"]


def test_receive_block_header_queues_tip_mismatch_for_ancestor_sync(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "header-tip-mismatch.sqlite3")
    block = {
        "height": 1,
        "previous_hash": "f" * 64,
        "block_hash": "a" * 64,
        "timestamp": "2026-05-12T00:00:00+00:00",
    }

    received = receive_block_header(block, source_peer_id="peer-a")

    assert received["status"] == "pending_missing_ancestors"
    assert "previous_hash" in received["reason"]


def test_sync_status_reports_replay_queue_metrics(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "sync-replay-metrics.sqlite3")
    block = {
        "height": 1,
        "previous_hash": "f" * 64,
        "block_hash": "a" * 64,
        "timestamp": "2026-05-12T00:00:00+00:00",
    }

    receive_block_header(block, source_peer_id="peer-a")
    sync_status = get_sync_status()
    replay_status = get_replay_status()

    assert sync_status["pending_replay_blocks"] == 1
    assert sync_status["replay"]["queue_size"] == 1
    assert sync_status["replay"]["header_queue_size"] == 1
    assert replay_status["queue_size"] == 1
    assert sync_status["sync_status"] == replay_status["sync_status"]
    assert "replay_stalled" in sync_status


def test_health_keeps_missing_ancestor_replay_recoverable(tmp_path, monkeypatch) -> None:
    block = _full_block_with_nonlocal_parent(
        tmp_path,
        monkeypatch,
        source_name="health-replay-missing-ancestor-source.sqlite3",
        target_name="health-replay-missing-ancestor.sqlite3",
    )
    receive_block_header(block, source_peer_id="peer-a")

    replay = replay_finalized_blocks()
    health = get_health_status()

    assert replay["status"] == "partial"
    assert replay["missing_ancestors"] == 1
    assert replay["errors"] == []
    assert replay["sync_status"] == "catching_up"
    assert health["sync_status"] == "catching_up"
    assert health["replay_stalled"] is False
    assert health["divergence_detected"] is False
    assert health["mining_ready"] is False
    assert health["can_assign_tasks"] is False


def test_orphan_detector_flags_local_parent_when_remote_certified_child_continues_other_hash(
    tmp_path,
    monkeypatch,
) -> None:
    _init_network_db(tmp_path, monkeypatch, "orphan-detector.sqlite3")
    miner_key = generate_keypair()
    miner = register_miner("orphan-detector-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    local_block = get_block(1)
    assert local_block is not None

    remote_parent_hash = "8" * 64
    remote_child_hash = "9" * 64
    timestamp = "2026-06-09T17:30:00+00:00"
    remote_parent = {
        "height": 1,
        "previous_hash": GENESIS_HASH,
        "block_hash": remote_parent_hash,
        "timestamp": timestamp,
        "finality_certificate": {
            "required_approvals": 3,
            "approval_count": 3,
            "certificate_hash": "a" * 64,
            "job_id": "job_remote_parent",
            "task_id": "task_remote_parent",
            "created_at": timestamp,
        },
    }
    remote_child = {
        "height": 2,
        "previous_hash": remote_parent_hash,
        "block_hash": remote_child_hash,
        "timestamp": timestamp,
        "finality_certificate": {
            "required_approvals": 3,
            "approval_count": 3,
            "certificate_hash": "b" * 64,
            "job_id": "job_remote_child",
            "task_id": "task_remote_child",
            "created_at": timestamp,
        },
    }
    with get_connection() as connection:
        for block in (remote_parent, remote_child):
            connection.execute(
                """
                INSERT INTO consensus_block_proposals (
                    proposal_id, block_hash, height, previous_hash, proposer_node_id,
                    status, payload, approvals, rejections, rejection_reason,
                    finalized_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'remote-node', 'pending_missing_ancestors', ?, 0, 0,
                        'proposal accepted but previous_hash is not local chain tip', NULL, ?, ?)
                """,
                (
                    sha256_text(f"proposal:{block['block_hash']}"),
                    block["block_hash"],
                    block["height"],
                    block["previous_hash"],
                    json.dumps(block, sort_keys=True),
                    timestamp,
                    timestamp,
                ),
            )

    candidates = list_orphan_candidates()

    assert candidates
    candidate = candidates[0]
    assert candidate["local_height"] == 1
    assert candidate["local_block_hash"] == local_block["block_hash"]
    assert candidate["remote_parent_hash"] == remote_parent_hash
    assert candidate["remote_parent_known"] is True
    assert candidate["strongest_child"]["block_hash"] == remote_child_hash
    assert candidate["strongest_child"]["certificate"]["quorum_met"] is True
    assert candidate["verdict"] == "remote_chain_has_certified_child"
    assert candidate["reorg_required"] is True

    plan = plan_orphan_reorg(max_depth=1)

    assert plan["can_apply"] is True
    assert plan["dry_run"] is True
    assert plan["reason"] == "ready"
    assert plan["selected"]["depth"] == 1
    assert plan["selected"]["local_orphan"]["block_hash"] == local_block["block_hash"]
    assert plan["selected"]["remote_parent"]["block_hash"] == remote_parent_hash
    assert plan["selected"]["remote_parent"]["certificate"]["quorum_met"] is True
    assert plan["selected"]["remote_child"]["block_hash"] == remote_child_hash
    assert plan["selected"]["remote_child"]["certificate"]["quorum_met"] is True
    assert [operation["step"] for operation in plan["selected"]["operations"]] == [
        "orphan_local_tip",
        "rewind_accounting_from_height",
        "import_remote_parent",
        "replay_remote_child",
    ]

    prepared = prepare_orphan_reorg(max_depth=1)

    assert prepared["prepared"] is True
    assert prepared["record_count"] == 3
    assert any(blocker.startswith("remote_parent_payload_missing:") for blocker in prepared["apply_blockers"])
    assert any(blocker.startswith("remote_child_payload_missing:") for blocker in prepared["apply_blockers"])
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT block_hash, height, parent_hash, branch_status, source
            FROM chain_branch_blocks
            ORDER BY height ASC, branch_status ASC, block_hash ASC
            """
        ).fetchall()
    branch_rows = [dict(row) for row in rows]
    assert {row["block_hash"] for row in branch_rows} == {
        local_block["block_hash"],
        remote_parent_hash,
        remote_child_hash,
    }
    assert {
        row["branch_status"]
        for row in branch_rows
        if row["block_hash"] == local_block["block_hash"]
    } == {"orphan_losing_branch"}
    assert {
        row["branch_status"]
        for row in branch_rows
        if row["block_hash"] in {remote_parent_hash, remote_child_hash}
    } == {"reorg_candidate"}


def test_receive_block_header_queues_certified_competing_tip_for_orphan_reorg(
    tmp_path,
    monkeypatch,
) -> None:
    _init_network_db(tmp_path, monkeypatch, "competing-tip-reorg-header.sqlite3")
    miner_key = generate_keypair()
    miner = register_miner("competing-tip-local-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    local_block = get_block(1)
    assert local_block is not None

    remote_parent = _certified_reorg_block(
        height=1,
        previous_hash=GENESIS_HASH,
        task_id="task_remote_parent_receive",
        job_id="job_remote_parent_receive",
    )
    assert remote_parent["block_hash"] != local_block["block_hash"]
    remote_child = _certified_reorg_block(
        height=2,
        previous_hash=remote_parent["block_hash"],
        task_id="task_remote_child_receive",
        job_id="job_remote_child_receive",
    )

    parent_received = receive_block_header(remote_parent, source_peer_id="peer-reorg")
    child_received = receive_block_header(remote_child, source_peer_id="peer-reorg")

    assert parent_received["status"] == "pending_missing_ancestors"
    assert "competing tip" in parent_received["reason"]
    assert child_received["status"] == "pending_missing_ancestors"

    plan = plan_orphan_reorg(max_depth=1)

    assert plan["can_apply"] is True
    assert plan["reason"] == "ready"
    assert plan["selected"]["remote_parent"]["source"] == "header"
    assert plan["selected"]["remote_parent"]["block_hash"] == remote_parent["block_hash"]
    assert plan["selected"]["remote_parent"]["certificate"]["quorum_met"] is True
    assert plan["selected"]["remote_child"]["source"] == "header"
    assert plan["selected"]["remote_child"]["block_hash"] == remote_child["block_hash"]
    assert plan["selected"]["remote_child"]["certificate"]["quorum_met"] is True


def test_orphan_reorg_apply_replaces_local_tip_with_certified_branch(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "orphan-reorg-apply.sqlite3")
    miner_key = generate_keypair()
    miner = register_miner("orphan-reorg-apply-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    local_block = get_block(1)
    assert local_block is not None

    remote_parent = _certified_reorg_block(
        height=1,
        previous_hash=GENESIS_HASH,
        task_id="task_remote_parent_apply",
        job_id="job_remote_parent_apply",
    )
    remote_child = _certified_reorg_block(
        height=2,
        previous_hash=remote_parent["block_hash"],
        task_id="task_remote_child_apply",
        job_id="job_remote_child_apply",
    )
    _queue_reorg_block_proposal(remote_parent)
    _queue_reorg_block_proposal(remote_child)

    plan = plan_orphan_reorg(max_depth=1)
    assert plan["can_apply"] is True
    prepared = prepare_orphan_reorg(max_depth=1)
    assert prepared["prepared"] is True
    assert prepared["apply_blockers"] == []

    result = apply_orphan_reorg(max_depth=1)

    assert result["applied"] is True
    assert result["new_tip"] == {"height": 2, "block_hash": remote_child["block_hash"]}
    assert [item["block_hash"] for item in result["imported"]] == [
        remote_parent["block_hash"],
        remote_child["block_hash"],
    ]
    assert get_block(1)["block_hash"] == remote_parent["block_hash"]
    assert get_block(2)["block_hash"] == remote_child["block_hash"]
    assert get_full_economic_audit()["valid"] is True
    with get_connection() as connection:
        local_task = connection.execute(
            "SELECT status, stale_reason FROM tasks WHERE task_id = ?",
            (local_block["task_id"],),
        ).fetchone()
        statuses = {
            row["block_hash"]: row["branch_status"]
            for row in connection.execute(
                "SELECT block_hash, branch_status FROM chain_branch_blocks"
            ).fetchall()
        }
    assert local_task["status"] == "stale"
    assert local_task["stale_reason"] == "orphaned by certified branch reorg"
    assert statuses[local_block["block_hash"]] == "orphaned"
    assert statuses[remote_parent["block_hash"]] == "canonical_after_reorg"
    assert statuses[remote_child["block_hash"]] == "canonical_after_reorg"


def test_orphan_reorg_apply_rolls_back_and_replays_safe_transfer(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "orphan-reorg-apply-transfer.sqlite3")
    miner_key = generate_keypair()
    miner = register_miner("orphan-reorg-transfer-miner", miner_key["public_key"])
    sender = create_wallet("reorg-sender")
    recipient = create_wallet("reorg-recipient")
    _fund_wallet_from_genesis(sender["address"], 2.0)
    tx = sign_transaction(
        private_key=sender["private_key"],
        public_key=sender["public_key"],
        tx_type="transfer",
        sender=sender["address"],
        recipient=recipient["address"],
        amount=1.0,
        nonce=1,
        fee=0.01,
        timestamp="2026-06-09T17:00:00+00:00",
    )
    submit_transaction(tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    local_block = get_block(1)
    assert local_block is not None
    assert local_block["tx_count"] == 1
    assert get_transaction(tx["tx_hash"])["status"] == "confirmed"
    assert get_balance_amount(sender["address"]) == pytest.approx(0.99)

    remote_parent = _certified_reorg_block(
        height=1,
        previous_hash=GENESIS_HASH,
        task_id="task_remote_parent_transfer_apply",
        job_id="job_remote_parent_transfer_apply",
        timestamp="2026-06-09T17:30:00+00:00",
        transactions=[tx],
    )
    remote_child = _certified_reorg_block(
        height=2,
        previous_hash=remote_parent["block_hash"],
        task_id="task_remote_child_transfer_apply",
        job_id="job_remote_child_transfer_apply",
        timestamp="2026-06-09T17:31:00+00:00",
    )
    _queue_reorg_block_proposal(remote_parent)
    _queue_reorg_block_proposal(remote_child)

    prepared = prepare_orphan_reorg(max_depth=1)
    assert prepared["prepared"] is True
    assert prepared["apply_blockers"] == []

    result = apply_orphan_reorg(max_depth=1)

    assert result["applied"] is True
    assert result["removed"]["released_transactions"]["tx_hashes"] == [tx["tx_hash"]]
    assert result["new_tip"] == {"height": 2, "block_hash": remote_child["block_hash"]}
    confirmed = get_transaction(tx["tx_hash"])
    assert confirmed["status"] == "confirmed"
    assert confirmed["block_height"] == 1
    assert get_block(1)["block_hash"] == remote_parent["block_hash"]
    assert get_block(2)["block_hash"] == remote_child["block_hash"]
    assert get_balance_amount(sender["address"]) == pytest.approx(0.99)
    assert get_balance_amount(recipient["address"]) == pytest.approx(1.0)
    assert get_balance_amount("remote-reorg-miner") == pytest.approx((2.51328 * 2) + 0.01)
    with get_connection() as connection:
        nonce = connection.execute(
            "SELECT nonce FROM account_nonces WHERE account_id = ?",
            (sender["address"],),
        ).fetchone()
    assert int(nonce["nonce"]) == 1
    assert get_full_economic_audit()["valid"] is True


def test_orphan_reorg_prepare_blocks_unsupported_remote_transaction_type(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "orphan-reorg-unsupported-remote-tx.sqlite3")
    miner_key = generate_keypair()
    miner = register_miner("orphan-reorg-unsupported-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    sender = create_wallet("reorg-staker")
    _fund_wallet_from_genesis(sender["address"], 2.0)
    stake_tx = sign_transaction(
        private_key=sender["private_key"],
        public_key=sender["public_key"],
        tx_type="stake",
        sender=sender["address"],
        amount=1.0,
        nonce=1,
        fee=0.01,
        timestamp="2026-06-09T17:00:00+00:00",
    )

    remote_parent = _certified_reorg_block(
        height=1,
        previous_hash=GENESIS_HASH,
        task_id="task_remote_parent_stake_apply",
        job_id="job_remote_parent_stake_apply",
        timestamp="2026-06-09T17:30:00+00:00",
        transactions=[stake_tx],
    )
    remote_child = _certified_reorg_block(
        height=2,
        previous_hash=remote_parent["block_hash"],
        task_id="task_remote_child_stake_apply",
        job_id="job_remote_child_stake_apply",
        timestamp="2026-06-09T17:31:00+00:00",
    )
    _queue_reorg_block_proposal(remote_parent)
    _queue_reorg_block_proposal(remote_child)

    prepared = prepare_orphan_reorg(max_depth=1)

    assert prepared["prepared"] is True
    assert any(
        blocker.startswith(f"remote_parent_unsupported_tx_type:{stake_tx['tx_hash']}:stake")
        for blocker in prepared["apply_blockers"]
    )


def test_canonical_blocks_get_branch_metadata_defaults(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "branch-metadata-defaults.sqlite3")
    miner_key = generate_keypair()
    miner = register_miner("branch-metadata-miner", miner_key["public_key"])

    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    block = get_block(1)
    assert block is not None
    assert block["parent_hash"] == block["previous_hash"]
    assert block["branch_id"] == "canonical"
    assert block["branch_status"] == "canonical"
    assert block["ancestor_height"] == 0
    assert block["ancestor_hash"] == block["previous_hash"]
    assert block["selected_at"] == block["timestamp"]

    with get_connection() as connection:
        stored = connection.execute(
            """
            SELECT parent_hash, branch_id, branch_status, ancestor_height, ancestor_hash, selected_at
            FROM blocks
            WHERE height = 1
            """
        ).fetchone()
    assert stored is not None
    assert stored["parent_hash"] == block["previous_hash"]
    assert stored["branch_id"] == "canonical"
    assert stored["branch_status"] == "canonical"
    assert stored["ancestor_height"] == 0
    assert stored["ancestor_hash"] == block["previous_hash"]
    assert stored["selected_at"] == block["timestamp"]


def test_orphan_reorg_plan_reports_no_candidates(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "orphan-reorg-empty.sqlite3")

    plan = plan_orphan_reorg()

    assert plan["can_apply"] is False
    assert plan["reason"] == "no_orphan_candidates"
    assert plan["candidate_count"] == 0
    assert plan["selected"] is None


def test_sync_blocks_drains_replay_backlog_with_bounded_batch(tmp_path, monkeypatch) -> None:
    block = _full_block_with_nonlocal_parent(
        tmp_path,
        monkeypatch,
        source_name="sync-replay-throttle-source.sqlite3",
        target_name="sync-replay-throttle.sqlite3",
    )
    receive_block_header(block, source_peer_id="peer-a")

    class EmptyBlocksResponse:
        def json(self) -> dict:
            return {"blocks": []}

    monkeypatch.setattr("app.services.network.REPLAY_BACKLOG_THRESHOLD", 0)
    monkeypatch.setattr("app.services.network.requests.get", lambda *args, **kwargs: EmptyBlocksResponse())

    result = sync_blocks_until("http://peer-a:8000", limit=10)

    assert result["replay"]["status"] == "partial"
    assert result["replay"]["reason"] == "replay backlog drained with bounded batch"
    assert result["replay"]["queue_size"] == 1
    assert result["replay"]["sync_status"] == "catching_up"
    assert result["replay"]["replay_stalled"] is False


def test_sync_blocks_retries_replay_when_active_status_is_stalled(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "sync-replay-active-stalled.sqlite3")
    replay_calls: list[int] = []

    class EmptyBlocksResponse:
        def json(self) -> dict:
            return {"blocks": []}

    def fake_replay_status() -> dict:
        return {
            "active": True,
            "queue_size": 3,
            "sync_status": "stalled",
            "replay_stalled": True,
            "divergence_detected": False,
        }

    def fake_replay_finalized_blocks(limit: int = 100) -> dict:
        replay_calls.append(limit)
        return {
            "status": "ok",
            "imported": 1,
            "headers_imported": 1,
            "headers_skipped": 0,
            "errors": [],
            "queue_size": 0,
            "sync_status": "healthy",
            "replay_stalled": False,
        }

    monkeypatch.setattr("app.services.consensus.get_replay_status", fake_replay_status)
    monkeypatch.setattr("app.services.consensus.replay_finalized_blocks", fake_replay_finalized_blocks)
    monkeypatch.setattr("app.services.network.requests.get", lambda *args, **kwargs: EmptyBlocksResponse())

    result = sync_blocks_until("http://peer-a:8000", limit=10)

    assert replay_calls == [10]
    assert result["replay"]["status"] == "ok"
    assert result["replay"]["sync_status"] == "healthy"


def test_sync_blocks_uses_reconcile_fetch_timeout(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "sync-blocks-reconcile-timeout.sqlite3")
    timeouts: list[float] = []

    class EmptyBlocksResponse:
        def json(self) -> dict:
            return {"blocks": []}

    def fake_get(url, timeout=0):
        if "/node/sync/blocks" in url:
            timeouts.append(timeout)
            return EmptyBlocksResponse()
        raise AssertionError(url)

    monkeypatch.setattr("app.services.network.RECONCILE_FETCH_TIMEOUT_SECONDS", 42.0)
    monkeypatch.setattr("app.services.network.requests.get", fake_get)

    result = sync_blocks_until("http://peer-a:8000", limit=10)

    assert result["blocks_seen"] == 0
    assert timeouts == [42.0]


def test_sync_blocks_skips_peer_fetch_when_replay_already_divergent(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "sync-replay-already-divergent.sqlite3")
    _queue_replay_backlog_for_divergence_guard()
    _mark_replay_divergent("state_root mismatch after canonical replay")

    def fail_get(*args, **kwargs):
        raise AssertionError("divergent replay should not fetch peer blocks")

    monkeypatch.setattr("app.services.network.requests.get", fail_get)

    result = sync_blocks_until("http://peer-a:8000", limit=10)

    assert result["blocks_seen"] == 0
    assert result["blocks_imported"] == 0
    assert result["replay"]["status"] == "skipped"
    assert result["replay"]["reason"] == "replay divergent; restore required"
    assert result["replay"]["sync_status"] == "divergent"
    assert result["replay"]["divergence_detected"] is True


def test_reconcile_peer_skips_network_when_replay_already_divergent(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "reconcile-replay-already-divergent.sqlite3")
    _queue_replay_backlog_for_divergence_guard()
    _mark_replay_divergent("state_root mismatch after canonical replay")

    def fail_get(*args, **kwargs):
        raise AssertionError("divergent replay should not contact reconcile peer")

    monkeypatch.setattr("app.services.network.requests.get", fail_get)

    result = reconcile_peer("http://peer-a:8000")

    assert result["identity_registered"] is False
    assert result["peers_seen"] == 0
    assert result["mempool_inventory_seen"] == 0
    assert result["blocks_seen"] == 0
    assert result["proposals_seen"] == 0
    assert result["replay"]["status"] == "skipped"
    assert result["replay"]["reason"] == "replay divergent; restore required"
    assert result["errors"] == ["replay divergent; restore required"]


def test_sync_blocks_can_trigger_opt_in_auto_recovery_after_divergence(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "sync-auto-recovery.sqlite3")
    _queue_replay_backlog_for_divergence_guard()
    _mark_replay_divergent("state_root mismatch after canonical replay")

    class EmptyBlocksResponse:
        def json(self) -> dict:
            return {"blocks": []}

    monkeypatch.setattr("app.services.network.AUTO_RECOVERY_ENABLED", True)
    monkeypatch.setattr("app.services.network.requests.get", lambda *args, **kwargs: EmptyBlocksResponse())
    monkeypatch.setattr(
        "app.services.network.recover_from_peer_snapshot",
        lambda peer_address, source="auto-recovery": {"status": "ok", "peer": peer_address, "source": source},
    )

    result = sync_blocks_until("http://peer-a:8000", limit=10)

    assert result["replay"]["status"] == "skipped"
    assert result["replay"]["sync_status"] == "divergent"
    assert result["auto_recovery"]["status"] == "ok"
    assert result["auto_recovery"]["peer"] == "http://peer-a:8000"


def test_peer_rejects_wrong_chain(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "wrong-chain.sqlite3")

    with pytest.raises(NetworkError, match="chain_id mismatch"):
        register_peer(
            node_id="bad-peer",
            peer_address="http://bad-peer:8000",
            peer_type="full",
            protocol_version=PROTOCOL_VERSION,
            network_id=NETWORK_ID,
            chain_id="other-chain",
            genesis_hash=GENESIS_HASH,
        )


def test_peer_accepts_network_id_drift_when_chain_and_genesis_match(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "network-drift.sqlite3")

    peer = register_peer(
        node_id="drift-peer",
        peer_address="http://drift-peer:8000",
        peer_type="validator",
        protocol_version=PROTOCOL_VERSION,
        network_id="public-testnet-restarted",
        chain_id=CHAIN_ID,
        genesis_hash=GENESIS_HASH,
    )

    assert peer["status"] == "connected"
    assert peer["network_id"] == NETWORK_ID
    assert peer["metadata"]["observed_network_id"] == "public-testnet-restarted"


def test_signed_transaction_enters_mempool_once(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "mempool.sqlite3")

    wallet = create_wallet("alice")
    recipient = create_wallet("bob")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=recipient["address"],
        amount=1.5,
        nonce=1,
        fee=0.01,
    )

    accepted = submit_transaction(tx)
    duplicate = submit_transaction(tx)
    mempool = list_mempool()

    assert accepted["tx_hash"] == tx["tx_hash"]
    assert duplicate["tx_hash"] == tx["tx_hash"]
    assert mempool[0]["status"] == "pending"
    assert mempool[0]["sender"] == wallet["address"]


def test_duplicate_nonce_is_rejected(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "duplicate-nonce.sqlite3")

    wallet = create_wallet("alice")
    first_recipient = create_wallet("bob")
    second_recipient = create_wallet("carol")
    first = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=first_recipient["address"],
        amount=1,
        nonce=7,
    )
    second = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=second_recipient["address"],
        amount=2,
        nonce=7,
    )

    submit_transaction(first)
    with pytest.raises(NetworkError, match="duplicate sender nonce"):
        submit_transaction(second)


def test_terminal_duplicate_transaction_is_idempotent(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "terminal-duplicate.sqlite3")

    wallet = create_wallet("alice")
    recipient = create_wallet("bob")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=recipient["address"],
        amount=1,
        nonce=1,
    )
    submit_transaction(tx)
    with get_connection() as connection:
        connection.execute(
            "UPDATE mempool_transactions SET status = 'confirmed', block_height = 1 WHERE tx_hash = ?",
            (tx["tx_hash"],),
        )

    duplicate = submit_transaction(tx, propagated=True)

    assert duplicate["tx_hash"] == tx["tx_hash"]
    assert duplicate["status"] == "confirmed"
    assert duplicate["block_height"] == 1


def test_failed_nonce_can_be_replaced_by_new_transaction(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "failed-nonce-replacement.sqlite3")

    wallet = create_wallet("alice")
    first_recipient = create_wallet("bob")
    second_recipient = create_wallet("carol")
    first = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=first_recipient["address"],
        amount=1,
        nonce=1,
    )
    second = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=second_recipient["address"],
        amount=1,
        nonce=1,
    )
    submit_transaction(first)
    with get_connection() as connection:
        connection.execute(
            "UPDATE mempool_transactions SET status = 'failed', failure_reason = 'test failure' WHERE tx_hash = ?",
            (first["tx_hash"],),
        )

    replacement = submit_transaction(second)

    with get_connection() as connection:
        first_row = connection.execute(
            "SELECT status FROM mempool_transactions WHERE tx_hash = ?",
            (first["tx_hash"],),
        ).fetchone()
    assert first_row is None
    assert replacement["tx_hash"] == second["tx_hash"]
    assert replacement["status"] == "pending"


def test_invalid_signature_is_rejected(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "invalid-signature.sqlite3")

    wallet = create_wallet("alice")
    recipient = create_wallet("bob")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=recipient["address"],
        amount=2,
        nonce=1,
    )
    tx["signature"] = "invalid"

    with pytest.raises(NetworkError, match="invalid transaction signature"):
        submit_transaction(tx)


def test_signed_transaction_request_preserves_signed_timestamp(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "signed-timestamp.sqlite3")

    wallet = create_wallet("alice")
    recipient = create_wallet("bob")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=recipient["address"],
        amount=1,
        nonce=1,
        timestamp="2026-05-14T12:00:00+00:00",
    )

    payload = SignedTransactionRequest(**tx).model_dump(mode="json")

    assert payload["timestamp"] == tx["timestamp"]
    submit_transaction(payload)


def test_transaction_expiration_uses_signed_timestamp_not_receive_time(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "signed-expiration.sqlite3")
    monkeypatch.setattr("app.services.network._now", lambda: "2026-05-14T12:30:00+00:00")

    wallet = create_wallet("alice")
    recipient = create_wallet("bob")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=recipient["address"],
        amount=1,
        nonce=1,
        timestamp="2026-05-14T12:00:00Z",
    )

    submit_transaction(tx)

    expected_expires_at = (
        datetime.fromisoformat("2026-05-14T12:00:00+00:00") + timedelta(seconds=MEMPOOL_TX_TTL_SECONDS)
    ).isoformat()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT created_at, expires_at FROM mempool_transactions WHERE tx_hash = ?",
            (tx["tx_hash"],),
        ).fetchone()

    assert row is not None
    assert row["created_at"] == "2026-05-14T12:30:00+00:00"
    assert row["expires_at"] == expected_expires_at


def test_nonce_zero_is_rejected_at_submission(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "nonce-zero.sqlite3")

    wallet = create_wallet("alice")
    recipient = create_wallet("bob")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=recipient["address"],
        amount=1,
        nonce=0,
    )

    with pytest.raises(NetworkError, match="nonce must be >= 1"):
        submit_transaction(tx)


def test_invalid_transfer_recipient_is_rejected_at_submission(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "invalid-recipient.sqlite3")

    wallet = create_wallet("alice")
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient="PIB",
        amount=1,
        nonce=1,
    )

    with pytest.raises(NetworkError, match="valid PI recipient"):
        submit_transaction(tx)


def test_next_block_previous_hash_mismatch_waits_for_ancestor_sync(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "invalid-block.sqlite3")

    received = receive_block_header(
        {
            "height": 1,
            "previous_hash": "1" * 64,
            "block_hash": "2" * 64,
            "timestamp": "2026-05-12T00:00:00+00:00",
        }
    )

    assert received["status"] == "pending_missing_ancestors"
    assert "previous_hash" in received["reason"]


def test_sync_status_reports_distributed_context(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "sync-status.sqlite3")

    status = get_sync_status()

    assert status["chain_id"] == CHAIN_ID
    assert status["network_id"] == NETWORK_ID
    assert status["latest_block_height"] == 0
    assert status["latest_block_hash"] == GENESIS_HASH


def test_mined_block_confirms_signed_transfer_with_transaction_merkle_root(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "block-transactions.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("tx-miner", miner_key["public_key"])
    sender = create_wallet("alice")
    recipient = create_wallet("bob")
    _fund_wallet_from_genesis(sender["address"], 2.0)

    tx = sign_transaction(
        private_key=sender["private_key"],
        public_key=sender["public_key"],
        tx_type="transfer",
        sender=sender["address"],
        recipient=recipient["address"],
        amount=1.0,
        nonce=1,
        fee=0.01,
    )
    submit_transaction(tx)

    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    block = get_block(1)
    confirmed = get_transaction(tx["tx_hash"])
    chain = verify_chain()

    assert block is not None
    assert block["tx_count"] == 1
    assert block["tx_hashes"] == [tx["tx_hash"]]
    assert block["tx_merkle_root"]
    assert len(block["state_root"]) == 64
    assert block["fee_reward"] == pytest.approx(0.01)
    assert confirmed is not None
    assert confirmed["status"] == "confirmed"
    assert confirmed["block_height"] == 1
    assert get_balance_amount(sender["address"]) == pytest.approx(0.99)
    assert get_balance_amount(recipient["address"]) == pytest.approx(1.0)
    assert get_balance_amount(miner["miner_id"]) == pytest.approx(2.51328 + 0.01)
    assert chain["valid"] is True


def test_candidate_block_replay_matches_across_nodes_with_local_mempool_drift(tmp_path, monkeypatch) -> None:
    first_sender = create_wallet("alice-candidate")
    second_sender = create_wallet("bob-candidate")
    first_recipient = create_wallet("carol-candidate")
    second_recipient = create_wallet("dave-candidate")
    miner_id = "miner_candidate_replay"
    first_tx = sign_transaction(
        private_key=first_sender["private_key"],
        public_key=first_sender["public_key"],
        tx_type="transfer",
        sender=first_sender["address"],
        recipient=first_recipient["address"],
        amount=1.0,
        nonce=1,
        fee=0.01,
        timestamp="2026-05-14T12:00:00+00:00",
    )
    second_tx = sign_transaction(
        private_key=second_sender["private_key"],
        public_key=second_sender["public_key"],
        tx_type="transfer",
        sender=second_sender["address"],
        recipient=second_recipient["address"],
        amount=1.0,
        nonce=1,
        fee=0.01,
        timestamp="2026-05-14T12:00:01+00:00",
    )
    block_timestamp = "2026-05-14T12:01:00+00:00"

    def replay_candidate(db_name: str, created_at_by_hash: dict[str, str]) -> dict:
        _init_network_db(tmp_path, monkeypatch, db_name)
        _fund_wallet_from_genesis(first_sender["address"], 2.0)
        _fund_wallet_from_genesis(second_sender["address"], 2.0)
        submit_transaction(first_tx)
        submit_transaction(second_tx)
        with get_connection() as connection:
            for tx_hash, created_at in created_at_by_hash.items():
                connection.execute(
                    "UPDATE mempool_transactions SET created_at = ? WHERE tx_hash = ?",
                    (created_at, tx_hash),
                )
            selected = select_block_transactions(connection, limit=10)
            commitment = transaction_commitment(selected)
            applied = apply_block_transactions(
                connection,
                miner_id=miner_id,
                block_height=1,
                transactions=selected,
                timestamp=block_timestamp,
            )
            state_root = calculate_state_root(connection, 1, block_timestamp)
        return {
            "miner_id": miner_id,
            "tx_hashes": [tx["tx_hash"] for tx in selected],
            "commitment": commitment,
            "applied": applied,
            "state_root": state_root,
        }

    node_a = replay_candidate(
        "candidate-replay-a.sqlite3",
        {
            first_tx["tx_hash"]: "2026-05-14T12:00:30+00:00",
            second_tx["tx_hash"]: "2026-05-14T12:00:00+00:00",
        },
    )
    node_b = replay_candidate(
        "candidate-replay-b.sqlite3",
        {
            first_tx["tx_hash"]: "2026-05-14T12:00:00+00:00",
            second_tx["tx_hash"]: "2026-05-14T12:00:30+00:00",
        },
    )

    assert node_a["miner_id"] == node_b["miner_id"]
    assert node_a["tx_hashes"] == node_b["tx_hashes"] == sorted([first_tx["tx_hash"], second_tx["tx_hash"]])
    assert node_a["commitment"] == node_b["commitment"]
    assert node_a["applied"] == node_b["applied"]
    assert node_a["state_root"] == node_b["state_root"]


def test_wallet_nonce_status_tracks_pending_and_confirmed_transactions(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "wallet-nonce.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("nonce-miner", miner_key["public_key"])
    sender = create_wallet("alice")
    recipient = create_wallet("bob")
    _fund_wallet_from_genesis(sender["address"], 2.0)

    with get_connection() as connection:
        initial = get_wallet_nonce_status(connection, sender["address"])
    assert initial["confirmed_nonce"] == 0
    assert initial["pending_nonce"] == 0
    assert initial["next_nonce"] == 1

    tx = sign_transaction(
        private_key=sender["private_key"],
        public_key=sender["public_key"],
        tx_type="transfer",
        sender=sender["address"],
        recipient=recipient["address"],
        amount=1.0,
        nonce=1,
        fee=0.01,
    )
    submit_transaction(tx)

    with get_connection() as connection:
        pending = get_wallet_nonce_status(connection, sender["address"])
    assert pending["confirmed_nonce"] == 0
    assert pending["pending_nonce"] == 1
    assert pending["next_nonce"] == 2
    assert pending["pending_count"] == 1

    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    with get_connection() as connection:
        confirmed = get_wallet_nonce_status(connection, sender["address"])
    assert confirmed["confirmed_nonce"] == 1
    assert confirmed["pending_nonce"] == 0
    assert confirmed["next_nonce"] == 2
    assert confirmed["pending_count"] == 0


def test_expired_nonce_gap_blocks_next_pending_transaction(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "wallet-expired-nonce-gap.sqlite3")

    sender = create_wallet("alice-expired-gap")
    recipient = create_wallet("bob-expired-gap")
    _fund_wallet_from_genesis(sender["address"], 3.0)

    expired_tx = sign_transaction(
        private_key=sender["private_key"],
        public_key=sender["public_key"],
        tx_type="transfer",
        sender=sender["address"],
        recipient=recipient["address"],
        amount=0.1,
        nonce=1,
        fee=0.01,
    )
    next_tx = sign_transaction(
        private_key=sender["private_key"],
        public_key=sender["public_key"],
        tx_type="transfer",
        sender=sender["address"],
        recipient=recipient["address"],
        amount=0.2,
        nonce=2,
        fee=0.01,
    )
    submit_transaction(expired_tx)
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE mempool_transactions
            SET status = 'expired', rejection_reason = 'ttl expired', updated_at = created_at
            WHERE tx_hash = ?
            """,
            (expired_tx["tx_hash"],),
        )
    submit_transaction(next_tx)

    with get_connection() as connection:
        nonce_status = get_wallet_nonce_status(connection, sender["address"])
        selected = select_block_transactions(connection, limit=1)

    assert nonce_status["confirmed_nonce"] == 0
    assert nonce_status["pending_nonce"] == 2
    assert nonce_status["next_nonce"] == 1
    assert selected == []


def test_block_transaction_selection_prioritizes_fee_without_reordering_sender_nonce(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "fee-priority.sqlite3")

    alice = create_wallet("alice")
    bob = create_wallet("bob")
    recipient = create_wallet("recipient")
    _fund_wallet_from_genesis(alice["address"], 5.0)
    _fund_wallet_from_genesis(bob["address"], 5.0)

    alice_first = sign_transaction(
        private_key=alice["private_key"],
        public_key=alice["public_key"],
        tx_type="transfer",
        sender=alice["address"],
        recipient=recipient["address"],
        amount=1.0,
        nonce=1,
        fee=0.01,
    )
    alice_second = sign_transaction(
        private_key=alice["private_key"],
        public_key=alice["public_key"],
        tx_type="transfer",
        sender=alice["address"],
        recipient=recipient["address"],
        amount=1.0,
        nonce=2,
        fee=0.50,
    )
    bob_first = sign_transaction(
        private_key=bob["private_key"],
        public_key=bob["public_key"],
        tx_type="transfer",
        sender=bob["address"],
        recipient=recipient["address"],
        amount=1.0,
        nonce=1,
        fee=0.10,
    )
    submit_transaction(alice_first)
    submit_transaction(alice_second)
    submit_transaction(bob_first)

    with get_connection() as connection:
        selected = select_block_transactions(connection, limit=3)

    assert [tx["tx_hash"] for tx in selected] == [
        bob_first["tx_hash"],
        alice_first["tx_hash"],
        alice_second["tx_hash"],
    ]


def test_state_root_detects_ledger_tampering(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "state-root-tamper.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("state-root-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    block = get_block(1)

    assert block is not None
    assert len(block["state_root"]) == 64
    assert verify_chain()["valid"] is True

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, amount
            FROM ledger_entries
            WHERE block_height = 1 AND entry_type = 'block_reward'
            LIMIT 1
            """
        ).fetchone()
        connection.execute(
            "UPDATE ledger_entries SET amount = ? WHERE id = ?",
            (round(float(row["amount"]) + 0.0001, 8), row["id"]),
        )

    tampered = verify_chain()

    assert tampered["valid"] is False
    assert any(issue["reason"] == "state_root does not match ledger replay" for issue in tampered["issues"])


def test_repair_missing_block_rewards_backfills_legacy_economics(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "repair-missing-rewards.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("repair-reward-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    block = get_block(1)

    assert block is not None
    with get_connection() as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM miners WHERE miner_id = ?", (block["miner_id"],))
        connection.execute("DELETE FROM rewards WHERE block_height = 1")
        connection.execute(
            """
            DELETE FROM ledger_entries
            WHERE block_height = 1 AND entry_type = 'block_reward'
            """
        )
        connection.execute(
            """
            UPDATE balances
            SET balance = ROUND(balance - ?, 8)
            WHERE account_id = ?
            """,
            (block["reward"], block["miner_id"]),
        )
        update_block_state_root(connection, 1, block["timestamp"])

    broken_audit = get_full_economic_audit()
    repaired = repair_missing_block_rewards()
    audit = get_full_economic_audit()
    chain = verify_chain()
    repaired_again = repair_missing_block_rewards()

    assert broken_audit["valid"] is False
    assert repaired["repaired_blocks"] == 1
    assert repaired["miners_restored"] == 1
    assert repaired["rewards_inserted"] == 1
    assert repaired["ledger_entries_inserted"] == 1
    assert repaired["audit_valid"] is True
    assert repaired["chain_valid"] is True
    assert audit["valid"] is True
    assert chain["valid"] is True
    assert repaired_again["repaired_blocks"] == 0


def test_canonical_checkpoint_can_be_created_and_verified(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "canonical-checkpoint.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("checkpoint-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    checkpoint = create_canonical_checkpoint(height=1, source="test")
    latest = latest_checkpoint()
    verification = verify_checkpoint(1)

    assert checkpoint["height"] == 1
    assert checkpoint["block_hash"] == get_block(1)["block_hash"]
    assert checkpoint["state_root"] == get_block(1)["state_root"]
    assert checkpoint["balances_hash"] == checkpoint["state_root"]
    assert len(checkpoint["snapshot_hash"]) == 64
    assert latest["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert verification["valid"] is True


def test_checkpoint_verification_detects_snapshot_tampering(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "canonical-checkpoint-tamper.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("checkpoint-tamper-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    create_canonical_checkpoint(height=1, source="test")

    with get_connection() as connection:
        connection.execute(
            "UPDATE canonical_checkpoints SET state_root = ? WHERE height = 1",
            ("f" * 64,),
        )

    verification = verify_checkpoint(1)

    assert verification["valid"] is False
    assert "state_root mismatch" in verification["issues"]


def test_canonical_snapshot_exports_and_imports_into_fresh_node(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-export-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-export-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)

    assert snapshot["valid"] is True
    assert snapshot["checkpoint"]["height"] == 1
    assert snapshot["balances"]
    assert validate_snapshot_document(snapshot)["valid"] is True

    _init_network_db(tmp_path, monkeypatch, "snapshot-import-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    imports = list_imported_snapshots()

    assert imported["imported"] is True
    assert imported["validation"]["valid"] is True
    assert imports[0]["snapshot_hash"] == snapshot["checkpoint"]["snapshot_hash"]
    assert imports[0]["source"] == "peer-a"


def test_canonical_snapshot_export_retries_invalid_racy_document(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-export-racy-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-export-racy-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    real_validate = validate_snapshot_document
    calls = {"count": 0}

    def flaky_validate(document: dict) -> dict:
        calls["count"] += 1
        result = real_validate(document)
        if calls["count"] == 1:
            return {**result, "valid": False, "issues": ["snapshot_hash mismatch"]}
        return result

    monkeypatch.setattr("app.services.state.validate_snapshot_document", flaky_validate)

    snapshot = export_canonical_snapshot(height=1)

    assert calls["count"] == 2
    assert snapshot["valid"] is True
    assert snapshot["issues"] == []
    assert real_validate(snapshot)["valid"] is True


def test_snapshot_restore_preserves_retargeted_protocol_params_for_replay(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-protocol-source.sqlite3")
    active_protocol_params_id = _retarget_active_protocol_for_test(segment_size=8, difficulty=0.03125)

    miner_key = generate_keypair()
    miner = register_miner("snapshot-protocol-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    next_block = get_blocks_since(1)["blocks"][0]

    assert snapshot["valid"] is True
    assert snapshot["checkpoint"]["protocol_params_count"] >= 2
    assert snapshot["checkpoint"]["retarget_events_count"] == 1
    assert any(
        params["id"] == active_protocol_params_id
        and params["active"] == 1
        and params["segment_size"] == 8
        and params["difficulty"] == pytest.approx(0.03125)
        for params in snapshot["protocol_params"]
    )
    assert next_block["protocol_params_id"] == active_protocol_params_id

    _init_network_db(tmp_path, monkeypatch, "snapshot-protocol-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-retarget")
    applied = apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    with get_connection() as connection:
        active = connection.execute(
            "SELECT id, segment_size, difficulty, active FROM protocol_params WHERE active = 1"
        ).fetchone()
        event_count = connection.execute("SELECT COUNT(*) AS count FROM retarget_events").fetchone()["count"]
    received = receive_block_header(next_block, source_peer_id="peer-retarget")
    replay = replay_finalized_blocks()
    imported_block = get_block(next_block["height"])
    chain = verify_chain()

    assert applied["protocol_params_applied"] == snapshot["checkpoint"]["protocol_params_count"]
    assert applied["retarget_events_applied"] == snapshot["checkpoint"]["retarget_events_count"]
    assert int(active["id"]) == active_protocol_params_id
    assert int(active["segment_size"]) == 8
    assert float(active["difficulty"]) == pytest.approx(0.03125)
    assert int(event_count) == 1
    assert received["status"] == "pending_replay"
    assert replay["headers_imported"] == 1
    assert replay["errors"] == []
    assert imported_block["block_hash"] == next_block["block_hash"]
    assert imported_block["protocol_params_id"] == active_protocol_params_id
    assert chain["valid"] is True


def test_snapshot_restore_preserves_pending_reward_maturity_for_replay(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.services.rewards.BLOCK_MATURITY_DEPTH", 2)
    monkeypatch.setattr("app.services.mining.BLOCK_MATURITY_DEPTH", 2)
    _init_network_db(tmp_path, monkeypatch, "snapshot-pending-reward-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-pending-reward-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=2)
    next_block = get_blocks_since(2)["blocks"][0]
    source_audit = get_full_economic_audit()

    assert snapshot["valid"] is True
    assert snapshot["checkpoint"]["pending_rewards_count"] == 2
    assert [reward["matures_at_height"] for reward in snapshot["pending_rewards"]] == [3, 4]
    assert source_audit["valid"] is True

    _init_network_db(tmp_path, monkeypatch, "snapshot-pending-reward-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-pending-rewards")
    applied = apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    received = receive_block_header(next_block, source_peer_id="peer-pending-rewards")
    replay = replay_finalized_blocks()
    imported_block = get_block(next_block["height"])
    target_audit = get_full_economic_audit()

    assert applied["pending_rewards_applied"] == 2
    assert received["status"] == "pending_replay"
    assert replay["headers_imported"] == 1
    assert replay["errors"] == []
    assert imported_block["block_hash"] == next_block["block_hash"]
    assert imported_block["state_root"] == next_block["state_root"]
    assert target_audit["valid"] is True, target_audit["issues"]


def test_active_snapshot_base_accepts_next_block_header(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-active-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-active-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    next_block = get_block(2)

    _init_network_db(tmp_path, monkeypatch, "snapshot-active-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    active = activate_imported_snapshot(imported["snapshot"]["snapshot_hash"])
    received = receive_block_header(next_block, source_peer_id="peer-a")
    status = get_sync_status()

    assert active["active"] is True
    assert received["status"] == "pending_replay"
    assert received["reason"] == "accepted after active snapshot base"
    assert status["active_snapshot_base"]["height"] == 1


def test_apply_snapshot_state_bootstraps_balances_for_fast_sync(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-apply-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-apply-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    expected_miner_balance = get_balance_amount(miner["miner_id"])

    _init_network_db(tmp_path, monkeypatch, "snapshot-apply-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    applied = apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    status = get_sync_status()
    chain = verify_chain()
    audit = get_full_economic_audit()

    assert applied["applied"] is True
    assert applied["balances_applied"] == snapshot["checkpoint"]["balances_count"]
    assert applied["snapshot"]["state_applied"] is True
    assert status["active_snapshot_base"]["state_applied"] is True
    assert status["effective_latest_block_height"] == 1
    assert status["effective_latest_block_hash"] == snapshot["checkpoint"]["block_hash"]
    assert get_balance_amount(miner["miner_id"]) == pytest.approx(expected_miner_balance)
    assert chain["valid"] is True
    assert chain["latest_block_hash"] == snapshot["checkpoint"]["block_hash"]
    assert audit["valid"] is True
    assert audit["supply"]["economic_base_total"] == pytest.approx(snapshot["checkpoint"]["total_balance"])


def test_apply_snapshot_state_restores_validator_registry_for_replay(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-validator-source.sqlite3")

    miner_key = generate_keypair()
    validator_key = generate_keypair()
    miner = register_miner("snapshot-validator-miner", miner_key["public_key"])
    reward_wallet = create_wallet("snapshot-validator-reward")
    stake_owner = create_wallet("snapshot-validator-owner")
    validator = register_validator("snapshot-validator", validator_key["public_key"], reward_wallet["address"])
    _fund_wallet_from_genesis(stake_owner["address"], 40.0)
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE validators
            SET stake_locked = 0,
                wallet_stake_locked = 0,
                stake_owner_address = NULL
            WHERE validator_id = ?
            """,
            (validator["validator_id"],),
        )
    stake_tx = sign_transaction(
        private_key=stake_owner["private_key"],
        public_key=stake_owner["public_key"],
        tx_type="stake",
        sender=stake_owner["address"],
        amount=31.416,
        nonce=1,
        fee=0.01,
        payload={"stake_type": "validator", "validator_id": validator["validator_id"]},
    )
    submit_transaction(stake_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE validators
            SET accepted_jobs = 7,
                total_validation_ms = 1234
            WHERE validator_id = ?
            """,
            (validator["validator_id"],),
        )

    snapshot = export_canonical_snapshot(height=1)
    validation = validate_snapshot_document(snapshot)

    assert snapshot["valid"] is True
    assert validation["valid"] is True
    assert snapshot["checkpoint"]["validators_count"] >= 1
    assert any(item["validator_id"] == validator["validator_id"] for item in snapshot["validators"])

    _init_network_db(tmp_path, monkeypatch, "snapshot-validator-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-validator-state")
    applied = apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])

    with get_connection() as connection:
        restored = connection.execute(
            """
            SELECT validator_id, public_key, reward_address, stake_locked, wallet_stake_locked,
                   stake_owner_address, accepted_jobs, total_validation_ms
            FROM validators
            WHERE validator_id = ?
            """,
            (validator["validator_id"],),
        ).fetchone()

    assert applied["validators_applied"] == snapshot["checkpoint"]["validators_count"]
    assert get_full_economic_audit()["valid"] is True
    assert restored is not None
    assert restored["public_key"] == validator_key["public_key"]
    assert restored["reward_address"] == reward_wallet["address"]
    assert restored["stake_locked"] == pytest.approx(31.416)
    assert restored["wallet_stake_locked"] == pytest.approx(31.416)
    assert restored["stake_owner_address"] == stake_owner["address"]
    assert restored["accepted_jobs"] == 7
    assert restored["total_validation_ms"] == 1234


def test_snapshot_export_refreshes_checkpoint_after_validator_state_changes(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-validator-refresh-source.sqlite3")

    miner_key = generate_keypair()
    validator_key = generate_keypair()
    miner = register_miner("snapshot-validator-refresh-miner", miner_key["public_key"])
    validator = register_validator("snapshot-validator-refresh", validator_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    stale_snapshot = export_canonical_snapshot(height=1)

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE validators
            SET accepted_jobs = accepted_jobs + 3,
                total_validation_ms = total_validation_ms + 987
            WHERE validator_id = ?
            """,
            (validator["validator_id"],),
        )

    refreshed_snapshot = export_canonical_snapshot(height=1)
    validation = validate_snapshot_document(refreshed_snapshot)

    assert stale_snapshot["checkpoint"]["height"] == refreshed_snapshot["checkpoint"]["height"]
    assert refreshed_snapshot["valid"] is True
    assert validation["valid"] is True
    assert validation["issues"] == []
    assert refreshed_snapshot["checkpoint"]["snapshot_hash"] != stale_snapshot["checkpoint"]["snapshot_hash"]

    _init_network_db(tmp_path, monkeypatch, "snapshot-validator-refresh-target.sqlite3")
    imported = import_canonical_snapshot(refreshed_snapshot, source="peer-validator-refresh")

    assert imported["validation"]["valid"] is True


def test_snapshot_import_accepts_raw_export_validator_hash(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-raw-validator-hash-source.sqlite3")

    miner_key = generate_keypair()
    validator_key = generate_keypair()
    miner = register_miner("snapshot-raw-validator-hash-miner", miner_key["public_key"])
    register_validator("snapshot-raw-validator-hash", validator_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    legacy_snapshot = json.loads(json.dumps(snapshot))
    checkpoint = legacy_snapshot["checkpoint"]
    height = int(checkpoint["height"])

    for validator in legacy_snapshot["validators"]:
        validator.pop("stake_locked_units", None)
        validator.pop("wallet_stake_locked_units", None)
        validator.pop("slashed_amount_units", None)
    checkpoint["validators_hash"] = sha256_text(
        canonical_json({"height": height, "validators": legacy_snapshot["validators"]})
    )
    payload = {
        "chain_id": checkpoint["chain_id"],
        "network_id": checkpoint["network_id"],
        "genesis_hash": checkpoint["genesis_hash"],
        "protocol_version": checkpoint["protocol_version"],
        "height": height,
        "block_hash": checkpoint["block_hash"],
        "previous_hash": checkpoint["previous_hash"],
        "state_root": checkpoint["state_root"],
        "balances_hash": checkpoint["balances_hash"],
        "balances_count": checkpoint["balances_count"],
        "ledger_entries_count": checkpoint["ledger_entries_count"],
        "total_balance": checkpoint["total_balance"],
        "total_balance_units": checkpoint["total_balance_units"],
    }
    for key in ("nonces", "validators", "protocol_params", "retarget_events", "pending_rewards"):
        hash_key = f"{key}_hash"
        count_key = f"{key}_count"
        if checkpoint.get(hash_key):
            payload[hash_key] = checkpoint[hash_key]
            payload[count_key] = checkpoint[count_key]
    checkpoint["snapshot_hash"] = sha256_text(canonical_json(payload))

    validation = validate_snapshot_document(legacy_snapshot)

    assert validation["valid"] is True, validation["issues"]
    assert validation["computed"]["raw_validators_hash"] == checkpoint["validators_hash"]
    assert validation["computed"]["normalized_validators_hash"] != checkpoint["validators_hash"]

    _init_network_db(tmp_path, monkeypatch, "snapshot-raw-validator-hash-target.sqlite3")
    imported = import_canonical_snapshot(legacy_snapshot, source="raw-validator-hash")

    assert imported["validation"]["valid"] is True


def test_restore_snapshot_state_replaces_existing_local_chain(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-restore-source.sqlite3")
    source_key = generate_keypair()
    source_miner = register_miner("snapshot-restore-source-miner", source_key["public_key"])
    _mine_legacy_block(source_miner["miner_id"], source_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)

    _init_network_db(tmp_path, monkeypatch, "snapshot-restore-target.sqlite3")
    local_key = generate_keypair()
    local_miner = register_miner("snapshot-restore-local-miner", local_key["public_key"])
    _mine_legacy_block(local_miner["miner_id"], local_key["private_key"])
    create_canonical_checkpoint(height=1, source="stale-local")
    imported = import_canonical_snapshot(snapshot, source="peer-restore")

    restored = restore_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    status = get_sync_status()
    chain = verify_chain()
    audit = get_full_economic_audit()

    assert restored["applied"] is True
    assert restored["replace_existing"] is True
    assert restored["cleared"]["canonical_checkpoints"] == 1
    assert restored["cleared"]["blocks"] == 1
    assert restored["cleared"]["tasks"] == 1
    assert status["latest_block_height"] == 0
    assert status["effective_latest_block_height"] == 1
    assert status["effective_latest_block_hash"] == snapshot["checkpoint"]["block_hash"]
    assert chain["valid"] is True
    assert chain["latest_block_hash"] == snapshot["checkpoint"]["block_hash"]
    assert audit["valid"] is True

    post_restore_key = generate_keypair()
    post_restore_miner = register_miner("snapshot-restore-next-miner", post_restore_key["public_key"])
    _mine_legacy_block(post_restore_miner["miner_id"], post_restore_key["private_key"])
    next_block = get_block(2)

    assert next_block is not None
    assert next_block["previous_hash"] == snapshot["checkpoint"]["block_hash"]


def test_cli_builds_valid_snapshot_from_sqlite_backup(tmp_path, monkeypatch) -> None:
    source_db_path = tmp_path / "sqlite-snapshot-source.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", source_db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", source_db_path)
    init_db(source_db_path)

    source_key = generate_keypair()
    source_miner = register_miner("sqlite-snapshot-miner", source_key["public_key"])
    _mine_legacy_block(source_miner["miner_id"], source_key["private_key"])

    snapshot = _snapshot_from_sqlite(source_db_path, 1)
    validation = validate_snapshot_document(snapshot)

    assert validation["valid"] is True
    assert snapshot["checkpoint"]["height"] == 1
    assert snapshot["checkpoint"]["block_hash"] == get_block(1)["block_hash"]


def test_init_db_does_not_reinsert_genesis_ledger_after_snapshot_apply(tmp_path, monkeypatch) -> None:
    source_db_path = tmp_path / "snapshot-init-cleanup-source.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", source_db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", source_db_path)
    init_db(source_db_path)

    miner_key = generate_keypair()
    miner = register_miner("snapshot-init-cleanup-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)

    target_db_path = tmp_path / "snapshot-init-cleanup-target.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", target_db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", target_db_path)
    init_db(target_db_path)
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, balance_after, entry_type,
                block_height, related_id, description, created_at
            )
            VALUES ('genesis', 'genesis', ?, ?, 'genesis', 0, 'genesis', 'legacy duplicate genesis', ?)
            """,
            (3.1416, 3.1416, "1970-01-01T00:00:00+00:00"),
        )

    init_db(target_db_path)
    audit = get_full_economic_audit()

    assert audit["valid"] is True
    assert "genesis" not in audit["ledger"]["by_entry_type"]
    assert audit["ledger"]["by_entry_type"]["snapshot_state_import"] == pytest.approx(
        snapshot["checkpoint"]["total_balance"]
    )


def test_genesis_allocations_are_applied_deterministically(tmp_path, monkeypatch) -> None:
    allocation = {
        "version": 1,
        "network_id": NETWORK_ID,
        "chain_id": CHAIN_ID,
        "created_at": "2026-05-14T12:48:51.500763Z",
        "allocations": [
            {
                "account_id": "PI340F7EEA37754C5F9C9ADE84D98F9B4AE10F0E",
                "account_type": "wallet",
                "amount": 1.0,
                "description": "public testnet wallet funding",
            }
        ],
    }
    allocation_file = tmp_path / "genesis.allocations.json"
    allocation_file.write_text(json.dumps(allocation), encoding="utf-8")
    monkeypatch.setattr("app.core.settings.GENESIS_ALLOCATIONS_FILE", str(allocation_file))
    monkeypatch.setattr("app.db.database.GENESIS_ALLOCATIONS_FILE", str(allocation_file))

    first_db = tmp_path / "first.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", first_db)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", first_db)
    init_db(first_db)
    with get_connection() as connection:
        first_root = calculate_state_root(connection, 0, "2026-05-14T12:58:53.705008Z")
        first_wallet = connection.execute(
            "SELECT balance FROM balances WHERE account_id = ?",
            ("PI340F7EEA37754C5F9C9ADE84D98F9B4AE10F0E",),
        ).fetchone()
        first_genesis = connection.execute("SELECT balance FROM balances WHERE account_id = 'genesis'").fetchone()

    second_db = tmp_path / "second.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", second_db)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", second_db)
    init_db(second_db)
    with get_connection() as connection:
        second_root = calculate_state_root(connection, 0, "2026-05-14T12:58:53.705008Z")
        allocation_total = connection.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries WHERE entry_type = 'genesis_allocation'"
        ).fetchone()

    assert first_wallet["balance"] == pytest.approx(1.0)
    assert first_genesis["balance"] == pytest.approx(2.1416)
    assert first_root == second_root
    assert allocation_total["total"] == pytest.approx(1.0)
    assert len(genesis_allocations_hash(allocation)) == 64


def test_reconcile_peer_fetches_blocks_after_active_snapshot_base(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-reconcile-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-reconcile-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    next_block = get_blocks_since(1)["blocks"][0]

    _init_network_db(tmp_path, monkeypatch, "snapshot-reconcile-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    activate_imported_snapshot(imported["snapshot"]["snapshot_hash"])

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, timeout):
        if url.endswith("/node/identity"):
            return FakeResponse(
                {
                    "node_id": "peer-a",
                    "peer_address": "http://peer-a:8000",
                    "peer_type": "full",
                    "protocol_version": PROTOCOL_VERSION,
                    "network_id": NETWORK_ID,
                    "chain_id": CHAIN_ID,
                    "genesis_hash": GENESIS_HASH,
                    "bootstrap_peers": [],
                }
            )
        if url.endswith("/node/peers"):
            return FakeResponse([])
        if url.endswith("/mempool/inventory?status=pending&limit=100"):
            return FakeResponse({"transactions": []})
        if url.endswith("/mempool?status=pending&limit=100"):
            return FakeResponse([])
        if url.endswith("/mempool?limit=100"):
            return FakeResponse([])
        if url.endswith("/validators/heartbeat/inventory?limit=100"):
            return FakeResponse({"heartbeats": []})
        if url.endswith("/tasks/inventory?limit=100"):
            return FakeResponse({"tasks": []})
        if url.endswith("/validation/jobs/inventory?status=pending&limit=100"):
            return FakeResponse({"jobs": []})
        if url.endswith("/validation/votes/inventory?limit=100"):
            return FakeResponse({"votes": []})
        if url.endswith("/consensus/proposals?limit=100"):
            return FakeResponse(
                [
                    {
                        "proposal_id": "snapshot-covered",
                        "proposer_node_id": "peer-a",
                        "payload": {
                            "height": 1,
                            "previous_hash": snapshot["checkpoint"]["previous_hash"],
                            "block_hash": snapshot["checkpoint"]["block_hash"],
                            "timestamp": "2026-05-12T00:00:00+00:00",
                        },
                    }
                ]
            )
        if "/node/sync/blocks?from_height=1" in url:
            return FakeResponse(
                {
                    "from_height": 1,
                    "count": 1,
                    "blocks": [next_block],
                }
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr("app.services.network.requests.get", fake_get)
    result = reconcile_peer("http://peer-a:8000")

    assert result["sync_from_height"] == 1
    assert result["blocks_seen"] == 1
    assert result["blocks_imported"] == 1
    assert result["proposals_seen"] == 1
    assert result["proposals_imported"] == 0
    assert result["errors"] == []


def test_reconcile_after_restore_starts_after_snapshot_and_skips_stale_headers(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-restore-catchup-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-restore-catchup-miner", miner_key["public_key"])
    for _ in range(3):
        _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=3)
    stale_block = get_block(2)
    for _ in range(2):
        _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    new_blocks = get_blocks_since(3)["blocks"]

    _init_network_db(tmp_path, monkeypatch, "snapshot-restore-catchup-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO network_block_headers (
                block_hash, height, previous_hash, source_peer_id, status, reason, payload, received_at
            )
            VALUES (?, ?, ?, 'peer-a', 'pending_replay', 'stale pre-snapshot header', ?, ?)
            """,
            (
                stale_block["block_hash"],
                stale_block["height"],
                stale_block["previous_hash"],
                json.dumps(stale_block, sort_keys=True),
                "2026-05-10T00:00:00+00:00",
            ),
        )

    requested_urls: list[str] = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, timeout):
        requested_urls.append(url)
        if url.endswith("/node/identity"):
            return FakeResponse(
                {
                    "node_id": "peer-a",
                    "peer_address": "http://peer-a:8000",
                    "peer_type": "full",
                    "protocol_version": PROTOCOL_VERSION,
                    "network_id": NETWORK_ID,
                    "chain_id": CHAIN_ID,
                    "genesis_hash": GENESIS_HASH,
                    "bootstrap_peers": [],
                }
            )
        if url.endswith("/node/peers"):
            return FakeResponse([])
        if url.endswith("/mempool/inventory?status=pending&limit=100"):
            return FakeResponse({"transactions": []})
        if url.endswith("/mempool?limit=100"):
            return FakeResponse([])
        if url.endswith("/validators/heartbeat/inventory?limit=100"):
            return FakeResponse({"heartbeats": []})
        if url.endswith("/consensus/proposals?limit=100"):
            return FakeResponse([])
        if "/node/sync/blocks?from_height=3" in url:
            return FakeResponse({"from_height": 3, "count": len(new_blocks), "blocks": new_blocks})
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr("app.services.network.requests.get", fake_get)
    result = reconcile_peer("http://peer-a:8000")
    status = get_sync_status()
    chain = verify_chain()

    assert any("/node/sync/blocks?from_height=3" in url for url in requested_urls)
    assert not any("/node/sync/blocks?from_height=0" in url for url in requested_urls)
    assert result["local_block_height"] == 0
    assert result["snapshot_height"] == 3
    assert result["catch_up_start_height"] == 3
    assert result["blocks_seen"] == 2
    assert result["blocks_imported"] == 2
    assert result["replay"]["headers_imported"] == 2
    assert result["replay"]["headers_skipped_pre_snapshot"] == 1
    assert status["effective_latest_block_height"] == 5
    assert status["pending_replay_blocks"] == 0
    assert chain["valid"] is True


def test_replay_imports_pending_headers_after_active_snapshot_base(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-header-replay-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-header-replay-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    next_block = get_blocks_since(1)["blocks"][0]
    expected_height = next_block["height"]
    expected_hash = next_block["block_hash"]

    _init_network_db(tmp_path, monkeypatch, "snapshot-header-replay-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    received = receive_block_header(next_block, source_peer_id="peer-a")
    replay = replay_finalized_blocks()
    status = get_sync_status()
    audit = get_full_economic_audit()

    assert received["status"] == "pending_replay"
    assert replay["headers_imported"] == 1
    assert replay["errors"] == []
    assert status["latest_block_height"] == expected_height
    assert status["latest_block_hash"] == expected_hash
    assert status["effective_latest_block_height"] == expected_height
    assert audit["valid"] is True


def test_consensus_debug_block_reports_matching_canonical_hash(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "debug-block-determinism.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("debug-block-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    debug = debug_block_determinism(1)

    assert debug["expected_hash"] == get_block(1)["block_hash"]
    assert debug["matched"] is True
    assert debug["hash_input"] == canonical_json(debug["canonical_payload"])
    assert any(candidate["matches"] for candidate in debug["candidates"])


def test_replay_restores_snapshot_protocol_params_id_after_snapshot_base(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-header-foreign-protocol-source.sqlite3")
    with get_connection() as connection:
        connection.execute("UPDATE protocol_params SET id = 9999 WHERE active = 1")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-header-foreign-protocol-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    next_block = get_blocks_since(1)["blocks"][0]

    assert next_block["protocol_params_id"] == 9999

    _init_network_db(tmp_path, monkeypatch, "snapshot-header-foreign-protocol-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    received = receive_block_header(next_block, source_peer_id="peer-a")
    replay = replay_finalized_blocks()
    imported_block = get_block(next_block["height"])
    chain = verify_chain()

    assert received["status"] == "pending_replay"
    assert replay["headers_imported"] == 1
    assert replay["errors"] == []
    assert imported_block["block_hash"] == next_block["block_hash"]
    assert imported_block["protocol_params_id"] == 9999
    assert chain["valid"] is True


def test_health_reports_active_snapshot_tip_on_validator_restore(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-health-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-health-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)

    _init_network_db(tmp_path, monkeypatch, "snapshot-health-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    monkeypatch.setattr("app.services.mining.NODE_TYPE", "validator")

    health = get_health_status()

    assert health["status"] == "ok"
    assert health["latest_block_height"] == snapshot["checkpoint"]["height"]
    assert health["latest_block_hash"] == snapshot["checkpoint"]["block_hash"]
    assert health["local_block_height"] == 0
    assert health["local_block_hash"] == "0" * 64


def test_replay_imports_multiple_headers_after_snapshot_base(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-multi-header-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-multi-header-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    for _ in range(4):
        _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    blocks = get_blocks_since(1)["blocks"]

    _init_network_db(tmp_path, monkeypatch, "snapshot-multi-header-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    statuses = [receive_block_header(block, source_peer_id="peer-a")["status"] for block in blocks]
    pre_replay_status = get_sync_status()
    replay = replay_finalized_blocks()
    status = get_sync_status()
    audit = get_full_economic_audit()

    assert statuses[0] == "pending_replay"
    assert "pending_missing_ancestors" in statuses[1:]
    assert pre_replay_status["pending_replay_blocks"] == 4
    assert replay["headers_imported"] == 4
    assert replay["errors"] == []
    assert status["latest_block_height"] == 5
    assert status["pending_replay_blocks"] == 0
    assert audit["valid"] is True


def test_replay_enriches_pending_header_from_matching_proposal_payload(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-header-enrich-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-header-enrich-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    full_block = get_blocks_since(1)["blocks"][0]
    sparse_header = {key: full_block[key] for key in ("height", "previous_hash", "block_hash", "timestamp")}

    _init_network_db(tmp_path, monkeypatch, "snapshot-header-enrich-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    receive_block_header(sparse_header, source_peer_id="peer-a")
    propose_block(full_block, "peer-a", gossip=False)
    replay = replay_finalized_blocks()
    audit = get_full_economic_audit()

    assert replay["headers_imported"] == 1
    assert replay["errors"] == []
    assert get_block(full_block["height"])["block_hash"] == full_block["block_hash"]
    assert audit["valid"] is True


def test_header_replay_marks_matching_proposal_imported(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-header-import-status-source.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-header-import-status-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    full_block = get_blocks_since(1)["blocks"][0]

    _init_network_db(tmp_path, monkeypatch, "snapshot-header-import-status-target.sqlite3")
    imported = import_canonical_snapshot(snapshot, source="peer-a")
    apply_imported_snapshot_state(imported["snapshot"]["snapshot_hash"])
    receive_block_header(full_block, source_peer_id="peer-a")
    proposal = propose_block(full_block, "peer-a", gossip=False)
    replay = replay_finalized_blocks()
    status = get_sync_status()

    assert replay["headers_imported"] == 1
    assert status["consensus"].get("pending", 0) == 0
    with get_connection() as connection:
        row = connection.execute(
            "SELECT status FROM consensus_block_proposals WHERE proposal_id = ?",
            (proposal["proposal_id"],),
        ).fetchone()
    assert row["status"] == "imported"


def test_replay_normalizes_existing_block_proposals(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "existing-proposal-normalize.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("existing-proposal-normalize-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    block = get_block(1)
    proposal = propose_block(block, "peer-a", gossip=False)
    with get_connection() as connection:
        connection.execute(
            "UPDATE consensus_block_proposals SET status = 'pending' WHERE proposal_id = ?",
            (proposal["proposal_id"],),
        )

    replay = replay_finalized_blocks()
    status = get_sync_status()

    assert replay["normalized"] == 1
    assert status["consensus"].get("pending", 0) == 0
    with get_connection() as connection:
        row = connection.execute(
            "SELECT status FROM consensus_block_proposals WHERE proposal_id = ?",
            (proposal["proposal_id"],),
        ).fetchone()
    assert row["status"] == "imported"


def test_canonical_snapshot_import_rejects_tampered_balances(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "snapshot-import-tamper.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("snapshot-tamper-miner", miner_key["public_key"])
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    snapshot = export_canonical_snapshot(height=1)
    snapshot["balances"][0]["balance"] = round(float(snapshot["balances"][0]["balance"]) + 0.01, 8)
    validation = validate_snapshot_document(snapshot)

    assert validation["valid"] is False
    assert "balances_hash mismatch" in validation["issues"]


def test_science_stake_and_job_create_transactions_are_canonical(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "science-transactions.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("science-tx-miner", miner_key["public_key"])
    wallet = create_wallet("researcher")
    _fund_wallet_from_genesis(wallet["address"], 3_142.0)

    stake_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="stake",
        sender=wallet["address"],
        amount=3_141.6,
        nonce=1,
        fee=0.01,
    )
    submit_transaction(stake_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    account = get_science_account(wallet["address"])
    confirmed_stake = get_transaction(stake_tx["tx_hash"])

    assert confirmed_stake is not None
    assert confirmed_stake["status"] == "confirmed"
    assert account is not None
    assert account["tier"] == "researcher"
    assert account["stake_amount"] == pytest.approx(3_141.6)
    assert get_balance_amount(f"science_stake:{wallet['address']}") == pytest.approx(3_141.6)

    job_payload = {
        "job_type": "ai_inference",
        "metadata_hash": "metadata_hash",
        "storage_pointer": "ipfs://payload",
        "max_compute_units": 0,
        "reward_per_compute_unit": 0,
        "max_reward": 0,
    }
    job_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="science_job_create",
        sender=wallet["address"],
        amount=0,
        nonce=2,
        fee=0.01,
        payload=job_payload,
    )
    submit_transaction(job_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    expected_job_id = f"science_job_{job_tx['tx_hash'][:16]}"
    job = get_science_job(expected_job_id)
    confirmed_job = get_transaction(job_tx["tx_hash"])
    chain = verify_chain()

    assert confirmed_job is not None
    assert confirmed_job["status"] == "confirmed"
    assert job is not None
    assert job["requester_address"] == wallet["address"]
    assert job["job_type"] == "ai_inference"
    assert job["metadata_hash"] == "metadata_hash"
    assert job["storage_pointer"] == "ipfs://payload"
    assert get_balance_amount(wallet["address"]) == pytest.approx(0.38)
    assert get_balance_amount(miner["miner_id"]) == pytest.approx((2.51328 * 2) + 0.02)
    assert chain["valid"] is True


def test_science_unstake_transaction_unlocks_when_no_jobs_are_active(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "science-unstake-transaction.sqlite3")

    miner_key = generate_keypair()
    miner = register_miner("science-unstake-miner", miner_key["public_key"])
    wallet = create_wallet("researcher")
    _fund_wallet_from_genesis(wallet["address"], 3_142.0)

    stake_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="stake",
        sender=wallet["address"],
        amount=3_141.6,
        nonce=1,
        fee=0.01,
    )
    submit_transaction(stake_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    unstake_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="unstake",
        sender=wallet["address"],
        amount=0,
        nonce=2,
        fee=0.01,
    )
    submit_transaction(unstake_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    account = get_science_account(wallet["address"])
    confirmed_unstake = get_transaction(unstake_tx["tx_hash"])
    chain = verify_chain()

    assert confirmed_unstake is not None
    assert confirmed_unstake["status"] == "confirmed"
    assert account is not None
    assert account["status"] == "unstaking"
    assert account["stake_amount"] == pytest.approx(0)
    assert get_balance_amount(f"science_stake:{wallet['address']}") == pytest.approx(0)
    assert get_balance_amount(wallet["address"]) == pytest.approx(3_141.98)
    assert get_balance_amount(miner["miner_id"]) == pytest.approx((2.51328 * 2) + 0.02)
    assert chain["valid"] is True


def test_validator_stake_transaction_locks_and_unlocks_wallet_collateral(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "validator-wallet-stake-transaction.sqlite3")

    miner_key = generate_keypair()
    validator_key = generate_keypair()
    miner = register_miner("validator-stake-miner", miner_key["public_key"])
    validator = register_validator("wallet-staked-validator", validator_key["public_key"])
    wallet = create_wallet("validator-staker")
    _fund_wallet_from_genesis(wallet["address"], 40.0)
    with get_connection() as connection:
        connection.execute(
            "UPDATE validators SET stake_locked = 0, wallet_stake_locked = 0, stake_owner_address = NULL WHERE validator_id = ?",
            (validator["validator_id"],),
        )

    stake_payload = {"stake_type": "validator", "validator_id": validator["validator_id"]}
    stake_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="stake",
        sender=wallet["address"],
        amount=31.416,
        nonce=1,
        fee=0.01,
        payload=stake_payload,
    )
    submit_transaction(stake_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    confirmed_stake = get_transaction(stake_tx["tx_hash"])
    with get_connection() as connection:
        staked = connection.execute(
            "SELECT stake_locked, wallet_stake_locked, stake_owner_address FROM validators WHERE validator_id = ?",
            (validator["validator_id"],),
        ).fetchone()
    assert confirmed_stake is not None
    assert confirmed_stake["status"] == "confirmed"
    assert staked["stake_locked"] == pytest.approx(31.416)
    assert staked["wallet_stake_locked"] == pytest.approx(31.416)
    assert staked["stake_owner_address"] == wallet["address"]
    assert get_balance_amount(validator["validator_id"]) == pytest.approx(31.416)
    assert get_balance_amount(wallet["address"]) == pytest.approx(8.574)

    with get_connection() as connection:
        connection.execute("UPDATE validators SET enabled = 0 WHERE validator_id = ?", (validator["validator_id"],))

    unstake_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="unstake",
        sender=wallet["address"],
        amount=0,
        nonce=2,
        fee=0.01,
        payload=stake_payload,
    )
    submit_transaction(unstake_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    confirmed_unstake = get_transaction(unstake_tx["tx_hash"])
    with get_connection() as connection:
        unstaked = connection.execute(
            "SELECT stake_locked, wallet_stake_locked, stake_owner_address FROM validators WHERE validator_id = ?",
            (validator["validator_id"],),
        ).fetchone()
    audit = get_full_economic_audit()

    assert confirmed_unstake is not None
    assert confirmed_unstake["status"] == "confirmed"
    assert unstaked["stake_locked"] == pytest.approx(0)
    assert unstaked["wallet_stake_locked"] == pytest.approx(0)
    assert unstaked["stake_owner_address"] is None
    assert get_balance_amount(validator["validator_id"]) == pytest.approx(0)
    assert get_balance_amount(wallet["address"]) == pytest.approx(39.98)
    assert audit["valid"] is True


def test_validator_wallet_stake_unstake_preserves_legacy_collateral(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "validator-wallet-stake-legacy-mix.sqlite3")

    miner_key = generate_keypair()
    validator_key = generate_keypair()
    miner = register_miner("validator-legacy-stake-miner", miner_key["public_key"])
    validator = register_validator("legacy-staked-validator", validator_key["public_key"])
    wallet = create_wallet("legacy-validator-staker")
    _fund_wallet_from_genesis(wallet["address"], 1.0)

    stake_payload = {"stake_type": "validator", "validator_id": validator["validator_id"]}
    stake_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="stake",
        sender=wallet["address"],
        amount=0.5,
        nonce=1,
        fee=0.01,
        payload=stake_payload,
    )
    submit_transaction(stake_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    with get_connection() as connection:
        staked = connection.execute(
            "SELECT stake_locked, wallet_stake_locked, stake_owner_address FROM validators WHERE validator_id = ?",
            (validator["validator_id"],),
        ).fetchone()
    assert staked["stake_locked"] == pytest.approx(31.916)
    assert staked["wallet_stake_locked"] == pytest.approx(0.5)
    assert staked["stake_owner_address"] == wallet["address"]

    unstake_tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="unstake",
        sender=wallet["address"],
        amount=0,
        nonce=2,
        fee=0.01,
        payload=stake_payload,
    )
    submit_transaction(unstake_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    with get_connection() as connection:
        unstaked = connection.execute(
            "SELECT stake_locked, wallet_stake_locked, stake_owner_address FROM validators WHERE validator_id = ?",
            (validator["validator_id"],),
        ).fetchone()
    audit = get_full_economic_audit()

    assert unstaked["stake_locked"] == pytest.approx(31.416)
    assert unstaked["wallet_stake_locked"] == pytest.approx(0)
    assert unstaked["stake_owner_address"] is None
    assert get_balance_amount(validator["validator_id"]) == pytest.approx(0)
    assert get_balance_amount(wallet["address"]) == pytest.approx(0.98)
    assert audit["valid"] is True


def test_science_reserve_governance_actions_are_canonical_transactions(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "science-governance-transactions.sqlite3")
    monkeypatch.setattr("app.services.science.SCIENCE_RESERVE_GOVERNANCE_TIMELOCK_SECONDS", 0)

    miner_key = generate_keypair()
    miner = register_miner("science-governance-miner", miner_key["public_key"])
    signer_one = create_wallet("signer-one")
    signer_two = create_wallet("signer-two")
    _fund_wallet_from_genesis(signer_one["address"], 1.0)
    _fund_wallet_from_genesis(signer_two["address"], 1.0)
    _set_science_governance_signers([signer_one["address"], signer_two["address"]])

    propose_tx = _governance_tx(signer_one, "propose_activation", 1)
    submit_transaction(propose_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    proposed = get_science_reserve_governance()

    assert proposed["status"] == "L2_PENDING"
    assert signer_one["address"] in proposed["approvals"]

    approve_tx = _governance_tx(signer_two, "approve_activation", 1)
    submit_transaction(approve_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    approved = get_science_reserve_governance()

    assert signer_two["address"] in approved["approvals"]

    execute_tx = _governance_tx(signer_one, "execute_activation", 2)
    submit_transaction(execute_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    active = get_science_reserve_governance()

    assert active["status"] == "L2_ACTIVE"
    assert active["payouts_enabled"] is True

    pause_tx = _governance_tx(signer_two, "pause", 2)
    submit_transaction(pause_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    paused = get_science_reserve_governance()

    assert paused["status"] == "EMERGENCY_PAUSED"
    assert paused["emergency_paused"] is True

    unpause_tx = _governance_tx(signer_one, "unpause", 3)
    submit_transaction(unpause_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    unpaused = get_science_reserve_governance()
    chain = verify_chain()

    assert unpaused["status"] == "L2_ACTIVE"
    assert unpaused["payouts_enabled"] is True
    assert get_transaction(unpause_tx["tx_hash"])["status"] == "confirmed"
    assert get_balance_amount(miner["miner_id"]) == pytest.approx((2.51328 * 5) + 0.05)
    assert chain["valid"] is True


def test_scientific_development_treasury_claim_is_canonical_transaction(tmp_path, monkeypatch) -> None:
    _init_network_db(tmp_path, monkeypatch, "treasury-claim-transaction.sqlite3")
    monkeypatch.setattr("app.services.treasury.SCIENTIFIC_DEVELOPMENT_UNLOCK_INTERVAL_DAYS", 0)

    miner_key = generate_keypair()
    miner = register_miner("treasury-claim-miner", miner_key["public_key"])
    owner = create_wallet("owner")
    treasury_wallet = create_wallet("treasury")
    _fund_wallet_from_genesis(owner["address"], 1.0)
    monkeypatch.setattr("app.services.treasury.SCIENTIFIC_DEVELOPMENT_GOVERNANCE_WALLET", owner["address"])
    monkeypatch.setattr("app.services.treasury.SCIENTIFIC_DEVELOPMENT_TREASURY_WALLET", treasury_wallet["address"])

    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    unlocked = get_scientific_development_treasury()
    assert unlocked["unlocked_balance"] == pytest.approx(0.094248)

    claim_tx = sign_transaction(
        private_key=owner["private_key"],
        public_key=owner["public_key"],
        tx_type="treasury_claim",
        sender=owner["address"],
        amount=0,
        nonce=1,
        fee=0.01,
        payload={"claim_to": treasury_wallet["address"], "claim_id": "owner_claim_round_1"},
    )
    submit_transaction(claim_tx)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])

    claimed = get_scientific_development_treasury()
    confirmed = get_transaction(claim_tx["tx_hash"])
    chain = verify_chain()

    assert confirmed is not None
    assert confirmed["status"] == "confirmed"
    assert claimed["total_claimed"] == pytest.approx(0.094248)
    assert claimed["unlocked_balance"] == pytest.approx(0.094248)
    assert get_balance_amount(treasury_wallet["address"]) == pytest.approx(0.094248)
    assert get_balance_amount(SCIENTIFIC_DEVELOPMENT_TREASURY_ACCOUNT_ID) == pytest.approx(0.094248)
    assert get_balance_amount(owner["address"]) == pytest.approx(0.99)
    assert get_balance_amount(miner["miner_id"]) == pytest.approx((2.51328 * 2) + 0.01)
    assert chain["valid"] is True

    duplicate = sign_transaction(
        private_key=owner["private_key"],
        public_key=owner["public_key"],
        tx_type="treasury_claim",
        sender=owner["address"],
        amount=0,
        nonce=2,
        fee=0.01,
        payload={"claim_to": treasury_wallet["address"], "claim_id": "owner_claim_round_1"},
    )
    submit_transaction(duplicate)
    _mine_legacy_block(miner["miner_id"], miner_key["private_key"])
    rejected = get_transaction(duplicate["tx_hash"])

    assert rejected is not None
    assert rejected["status"] == "failed"
    assert "already exists" in rejected["rejection_reason"]


def _fund_wallet_from_genesis(address: str, amount: float) -> None:
    timestamp = "2026-05-12T00:00:00+00:00"
    with get_connection() as connection:
        genesis = connection.execute("SELECT balance FROM balances WHERE account_id = 'genesis'").fetchone()
        genesis_after = round(float(genesis["balance"]) - amount, 8)
        amount_units = to_units(amount)
        genesis_after_units = to_units(genesis_after)
        connection.execute(
            "UPDATE balances SET balance = ?, updated_at = ? WHERE account_id = 'genesis'",
            (genesis_after, timestamp),
        )
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, amount_units, balance_after, balance_after_units, entry_type,
                block_height, related_id, description, created_at
            )
            VALUES ('genesis', 'genesis', ?, ?, ?, ?, 'test_wallet_funding', NULL, ?, 'test wallet funding debit', ?)
            """,
            (-amount, -amount_units, genesis_after, genesis_after_units, address, timestamp),
        )
        connection.execute(
            """
            INSERT INTO balances (account_id, account_type, balance, balance_units, updated_at)
            VALUES (?, 'wallet', ?, ?, ?)
            """,
            (address, amount, amount_units, timestamp),
        )
        connection.execute(
            """
            INSERT INTO ledger_entries (
                account_id, account_type, amount, amount_units, balance_after, balance_after_units, entry_type,
                block_height, related_id, description, created_at
            )
            VALUES (?, 'wallet', ?, ?, ?, ?, 'test_wallet_funding', NULL, 'genesis', 'test wallet funding credit', ?)
            """,
            (address, amount, amount_units, amount, amount_units, timestamp),
        )


def _set_science_governance_signers(signers: list[str]) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE science_reserve_governance SET authorized_signers = ?, updated_at = ? WHERE id = 1",
            (json.dumps(signers), "2026-05-12T00:00:00+00:00"),
        )


def _governance_tx(wallet: dict, action: str, nonce: int) -> dict:
    return sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="governance_action",
        sender=wallet["address"],
        amount=0,
        nonce=nonce,
        fee=0.01,
        payload={"scope": "science_reserve", "action": action},
    )


def _mine_legacy_block(miner_id: str, private_key: str) -> None:
    task = create_next_task(miner_id)
    segment = calculate_pi_segment(task["range_start"], task["range_end"], task["algorithm"])
    result_hash = hash_result(segment, task["range_start"], task["range_end"], task["algorithm"])
    signed_at = "2026-05-12T00:00:00+00:00"
    payload = build_submission_signature_payload(
        task_id=task["task_id"],
        miner_id=miner_id,
        range_start=task["range_start"],
        range_end=task["range_end"],
        algorithm=task["algorithm"],
        result_hash=result_hash,
        signed_at=signed_at,
    )
    signature = sign_payload(private_key, payload)
    response = submit_task(task["task_id"], miner_id, result_hash, segment, signature, signed_at)
    assert response["accepted"] is True
