from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from app.core.crypto import sha256_text
from app.core.money import canonical_amount, to_units, units_to_float
from app.core.settings import (
    AUTO_RECOVERY_ENABLED,
    BOOTSTRAP_PEERS,
    CHAIN_ID,
    GENESIS_HASH,
    GOSSIP_ENABLED,
    GOSSIP_MAX_PEERS,
    GOSSIP_TIMEOUT_SECONDS,
    MAX_MEMPOOL_TXS,
    MAX_MEMPOOL_TXS_PER_ACCOUNT,
    MAX_TX_SIZE_BYTES,
    MEMPOOL_MAX_FEE,
    MEMPOOL_TX_TTL_SECONDS,
    MIN_TX_FEE_UNITS,
    NETWORK_ID,
    NODE_ID,
    NODE_PUBLIC_ADDRESS,
    NODE_TYPE,
    PEER_DISCOVERY_ENABLED,
    PEER_DISCOVERY_INTERVAL_SECONDS,
    PEER_DISCOVERY_MAX_PEERS,
    PEER_NETWORK_ID_TOLERANCE,
    PEER_TIMEOUT_SECONDS,
    PROJECT_NAME,
    PROTOCOL_VERSION,
    REPLAY_BACKLOG_THRESHOLD,
    REPLAY_BATCH_SIZE,
)
from app.core.signatures import verify_payload_signature
from app.db.database import get_connection, row_to_dict
from app.services.state import (
    active_snapshot_base_in_connection,
    import_canonical_snapshot,
    latest_checkpoint_in_connection,
    restore_imported_snapshot_state,
)
from app.services.wallet import (
    address_matches_public_key,
    is_valid_address,
    matching_transaction_signature_payload,
    unsigned_transaction_payload,
)


logger = logging.getLogger(__name__)

PEER_STALE_MARK_MIN_INTERVAL_SECONDS = int(os.getenv("PICOIN_PEER_STALE_MARK_MIN_INTERVAL_SECONDS", "60"))
PEER_REGISTER_MIN_INTERVAL_SECONDS = int(os.getenv("PICOIN_PEER_REGISTER_MIN_INTERVAL_SECONDS", "60"))
_PEER_STALE_MARK_LOCK = threading.Lock()
_PEER_STALE_MARK_LAST_RUN_MONOTONIC = 0.0
_PEER_REGISTER_LOCK = threading.Lock()
_PEER_REGISTER_LAST_RUN_MONOTONIC_BY_ID: dict[str, float] = {}


class NetworkError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


ALLOWED_NODE_TYPES = {"full", "miner", "validator", "auditor", "bootstrap"}
ALLOWED_TX_TYPES = {"transfer", "stake", "unstake", "science_job_create", "governance_action", "treasury_claim", "faucet"}
TERMINAL_TX_STATUSES = {"confirmed", "rejected", "failed", "expired"}
_PEER_DISCOVERY_TASK: asyncio.Task | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _normalize_peer_address(peer_address: str | None) -> str:
    address = str(peer_address or "").strip()
    if not address:
        return ""
    lower = address.lower()
    while lower.startswith("http://http://") or lower.startswith("https://https://"):
        if lower.startswith("http://http://"):
            address = address[len("http://") :]
        elif lower.startswith("https://https://"):
            address = address[len("https://") :]
        address = address.lstrip()
        lower = address.lower()
    address = address.rstrip("/")
    parsed = urlparse(address)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def recover_from_peer_snapshot(
    peer_address: str,
    *,
    height: int | None = None,
    source: str = "auto-recovery",
) -> dict[str, Any]:
    peer_url = peer_address.rstrip("/")
    path = "/node/snapshots/export"
    if height is not None:
        path = f"{path}?height={int(height)}"
    try:
        from app.services.consensus import clear_replay_liveness_status, set_replay_auto_recovery_active

        set_replay_auto_recovery_active(True)
        response = requests.get(
            f"{peer_url}{path}",
            timeout=max(10.0, float(GOSSIP_TIMEOUT_SECONDS) * 5),
        )
        response.raise_for_status()
        snapshot = response.json()
        imported = import_canonical_snapshot(snapshot, source=source)
        snapshot_hash = imported.get("snapshot", {}).get("snapshot_hash") or snapshot.get("checkpoint", {}).get("snapshot_hash")
        restored = restore_imported_snapshot_state(snapshot_hash)
        status = "ok" if restored.get("applied") else "fail"
        if status == "ok":
            clear_replay_liveness_status("catching_up")
        return {
            "status": status,
            "peer": peer_url,
            "height": restored.get("height"),
            "snapshot_hash": snapshot_hash,
            "import": imported,
            "restore": restored,
            "checked_at": _now(),
        }
    except Exception as exc:
        return {
            "status": "error",
            "peer": peer_url,
            "height": height,
            "error": str(exc),
            "checked_at": _now(),
        }
    finally:
        try:
            from app.services.consensus import set_replay_auto_recovery_active

            set_replay_auto_recovery_active(False)
        except Exception:
            pass


def node_identity() -> dict[str, Any]:
    return {
        "project": PROJECT_NAME,
        "node_id": NODE_ID,
        "peer_id": sha256_text(f"{CHAIN_ID}:{NODE_PUBLIC_ADDRESS.rstrip('/')}")[:32],
        "peer_address": NODE_PUBLIC_ADDRESS.rstrip("/"),
        "peer_type": NODE_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "network_id": NETWORK_ID,
        "chain_id": CHAIN_ID,
        "genesis_hash": GENESIS_HASH,
        "bootstrap_peers": list(BOOTSTRAP_PEERS),
    }


def register_peer(
    *,
    node_id: str,
    peer_address: str,
    peer_type: str = "full",
    protocol_version: str = PROTOCOL_VERSION,
    network_id: str = NETWORK_ID,
    chain_id: str | int = CHAIN_ID,
    genesis_hash: str = GENESIS_HASH,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    peer_type = peer_type.lower().strip()
    if peer_type not in ALLOWED_NODE_TYPES:
        raise NetworkError(422, "invalid peer_type")
    metadata = dict(metadata or {})
    if network_id != NETWORK_ID:
        if not (PEER_NETWORK_ID_TOLERANCE and str(chain_id) == str(CHAIN_ID) and genesis_hash == GENESIS_HASH):
            raise NetworkError(409, "peer network_id mismatch")
        metadata["observed_network_id"] = network_id
        metadata["accepted_network_id"] = NETWORK_ID
        metadata["network_id_warning"] = "accepted because chain_id and genesis_hash match"
        network_id = NETWORK_ID
    if str(chain_id) != str(CHAIN_ID):
        raise NetworkError(409, "peer chain_id mismatch")
    if genesis_hash != GENESIS_HASH:
        raise NetworkError(409, "peer genesis_hash mismatch")
    peer_address = _normalize_peer_address(peer_address)
    if not node_id.strip() or not peer_address:
        raise NetworkError(422, "node_id and peer_address are required")

    timestamp = _now()
    peer_id = sha256_text(f"{chain_id}:{peer_address}")[:32]
    source = str(metadata.get("source") or "")
    if source == "validator_heartbeat":
        monotonic_now = time.monotonic()
        with _PEER_REGISTER_LOCK:
            last_run = _PEER_REGISTER_LAST_RUN_MONOTONIC_BY_ID.get(peer_id, 0.0)
            if monotonic_now - last_run < PEER_REGISTER_MIN_INTERVAL_SECONDS:
                return {
                    "peer_id": peer_id,
                    "node_id": node_id,
                    "peer_address": peer_address,
                    "peer_type": peer_type,
                    "protocol_version": protocol_version,
                    "network_id": network_id,
                    "chain_id": chain_id,
                    "genesis_hash": genesis_hash,
                    "status": "connected",
                    "metadata": metadata,
                    "skipped": "throttled",
                }
            _PEER_REGISTER_LAST_RUN_MONOTONIC_BY_ID[peer_id] = monotonic_now
    try:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO network_peers (
                    peer_id, node_id, peer_address, peer_type, protocol_version,
                    network_id, chain_id, genesis_hash, connected_at, last_seen,
                    status, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'connected', ?)
                ON CONFLICT(peer_id) DO UPDATE SET
                    node_id = excluded.node_id,
                    peer_address = excluded.peer_address,
                    peer_type = excluded.peer_type,
                    protocol_version = excluded.protocol_version,
                    network_id = excluded.network_id,
                    chain_id = excluded.chain_id,
                    genesis_hash = excluded.genesis_hash,
                    last_seen = excluded.last_seen,
                    status = 'connected',
                    metadata = excluded.metadata
                """,
                (
                    peer_id,
                    node_id,
                    peer_address,
                    peer_type,
                    protocol_version,
                    network_id,
                    chain_id,
                    genesis_hash,
                    timestamp,
                    timestamp,
                    json.dumps(metadata, sort_keys=True),
                ),
            )
            if source != "validator_heartbeat":
                _record_sync_event(connection, peer_id, "peer_registered", "inbound", "accepted", {"peer_address": peer_address})
            peer = row_to_dict(connection.execute("SELECT * FROM network_peers WHERE peer_id = ?", (peer_id,)).fetchone())
        return _decode_peer(peer)
    except sqlite3.OperationalError as exc:
        if "database is locked" in str(exc).lower():
            logger.warning("peer registration skipped: database is locked peer_id=%s source=%s", peer_id, source or "unknown")
            return {
                "peer_id": peer_id,
                "node_id": node_id,
                "peer_address": peer_address,
                "peer_type": peer_type,
                "protocol_version": protocol_version,
                "network_id": network_id,
                "chain_id": chain_id,
                "genesis_hash": genesis_hash,
                "status": "unknown",
                "metadata": metadata,
                "skipped": "database_locked",
            }
        raise


def discover_peers(seed_peers: list[str] | None = None, *, limit: int | None = None) -> dict[str, Any]:
    """Discover one-hop peers from configured seeds and already connected peers."""

    max_peers = max(1, int(limit or PEER_DISCOVERY_MAX_PEERS))
    seeds: list[str] = []
    for peer in (seed_peers or []):
        normalized = _normalize_peer_address(peer)
        if normalized:
            seeds.append(normalized)
    for peer in BOOTSTRAP_PEERS:
        normalized = _normalize_peer_address(peer)
        if normalized:
            seeds.append(normalized)
    try:
        seeds.extend(
            _normalize_peer_address(peer["peer_address"]) for peer in list_peers(include_stale=False) if _normalize_peer_address(peer["peer_address"])
        )
    except Exception:
        pass
    local_address = NODE_PUBLIC_ADDRESS.rstrip("/")
    queue = list(dict.fromkeys(peer for peer in seeds if peer and peer != local_address))
    result = {
        "status": "ok",
        "attempted": 0,
        "registered": 0,
        "peers_seen": 0,
        "errors": [],
        "checked_at": _now(),
    }
    for peer_address in queue[:max_peers]:
        result["attempted"] += 1
        try:
            identity_response = requests.get(f"{peer_address}/node/identity", timeout=GOSSIP_TIMEOUT_SECONDS)
            identity_response.raise_for_status()
            identity = identity_response.json()
            register_peer(
                node_id=identity["node_id"],
                peer_address=identity["peer_address"],
                peer_type=identity["peer_type"],
                protocol_version=identity["protocol_version"],
                network_id=identity["network_id"],
                chain_id=identity["chain_id"],
                genesis_hash=identity["genesis_hash"],
                metadata={"source": "peer_discovery", "seed": peer_address},
            )
            result["registered"] += 1
        except Exception as exc:
            result["errors"].append(f"identity {peer_address}: {exc}")
            continue

        try:
            peers_response = requests.get(f"{peer_address}/node/peers", timeout=GOSSIP_TIMEOUT_SECONDS)
            peers_response.raise_for_status()
            for peer in peers_response.json():
                result["peers_seen"] += 1
                discovered_address = _normalize_peer_address(str(peer.get("peer_address") or ""))
                if not discovered_address or discovered_address == local_address:
                    continue
                try:
                    register_peer(
                        node_id=peer["node_id"],
                        peer_address=discovered_address,
                        peer_type=peer["peer_type"],
                        protocol_version=peer["protocol_version"],
                        network_id=peer["network_id"],
                        chain_id=peer["chain_id"],
                        genesis_hash=peer["genesis_hash"],
                        metadata={"source": "peer_discovery", "via": peer_address},
                    )
                    result["registered"] += 1
                except Exception as exc:
                    result["errors"].append(f"peer {discovered_address}: {exc}")
        except Exception as exc:
            result["errors"].append(f"peers {peer_address}: {exc}")
    if result["errors"]:
        result["status"] = "partial" if result["registered"] else "error"
    return result


async def start_peer_discovery_worker() -> None:
    global _PEER_DISCOVERY_TASK
    if not PEER_DISCOVERY_ENABLED or _PEER_DISCOVERY_TASK is not None:
        return
    _PEER_DISCOVERY_TASK = asyncio.create_task(_peer_discovery_loop())


async def stop_peer_discovery_worker() -> None:
    global _PEER_DISCOVERY_TASK
    task = _PEER_DISCOVERY_TASK
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    _PEER_DISCOVERY_TASK = None


async def _peer_discovery_loop() -> None:
    while True:
        try:
            discover_peers()
        except Exception:
            pass
        await asyncio.sleep(max(30.0, float(PEER_DISCOVERY_INTERVAL_SECONDS)))


def list_peers(include_stale: bool = True) -> list[dict[str, Any]]:
    _mark_stale_peers()
    query = "SELECT * FROM network_peers"
    params: tuple[Any, ...] = ()
    if not include_stale:
        query += " WHERE status = 'connected'"
    query += " ORDER BY last_seen DESC"
    with get_connection() as connection:
        return [_decode_peer(row_to_dict(row)) for row in connection.execute(query, params).fetchall()]


def select_reconcile_peers(limit: int = 16) -> list[dict[str, Any]]:
    """Return unique, locally compatible peers for read-only catch-up/reconcile."""

    max_peers = max(1, int(limit))
    local_address = _normalize_peer_address(NODE_PUBLIC_ADDRESS)
    selected: list[dict[str, Any]] = []
    seen_addresses: set[str] = set()
    for peer in list_peers(include_stale=False):
        peer_address = _normalize_peer_address(peer.get("peer_address"))
        if not peer_address or peer_address == local_address or peer_address in seen_addresses:
            continue
        if peer.get("network_id") != NETWORK_ID:
            continue
        if str(peer.get("chain_id")) != str(CHAIN_ID):
            continue
        if peer.get("genesis_hash") != GENESIS_HASH:
            continue
        if peer.get("protocol_version") != PROTOCOL_VERSION:
            continue
        normalized_peer = dict(peer)
        normalized_peer["peer_address"] = peer_address
        selected.append(normalized_peer)
        seen_addresses.add(peer_address)
        if len(selected) >= max_peers:
            break
    return selected


def heartbeat_peer(peer_id: str) -> dict[str, Any]:
    timestamp = _now()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM network_peers WHERE peer_id = ?", (peer_id,)).fetchone()
        if row is None:
            raise NetworkError(404, "peer not found")
        connection.execute(
            "UPDATE network_peers SET last_seen = ?, status = 'connected' WHERE peer_id = ?",
            (timestamp, peer_id),
        )
        _record_sync_event(connection, peer_id, "peer_heartbeat", "inbound", "accepted", {})
        peer = row_to_dict(connection.execute("SELECT * FROM network_peers WHERE peer_id = ?", (peer_id,)).fetchone())
    return _decode_peer(peer)


def get_sync_status() -> dict[str, Any]:
    _mark_stale_peers()
    with get_connection() as connection:
        latest_block = connection.execute(
            """
            SELECT height, block_hash, previous_hash, timestamp
            FROM blocks
            ORDER BY height DESC
            LIMIT 1
            """
        ).fetchone()
        peer_counts = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN status = 'connected' THEN 1 ELSE 0 END), 0) AS connected,
                COALESCE(SUM(CASE WHEN status = 'stale' THEN 1 ELSE 0 END), 0) AS stale
            FROM network_peers
            """
        ).fetchone()
        mempool_counts = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM mempool_transactions
            GROUP BY status
            """
        ).fetchall()
        pending_headers = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM network_block_headers
            WHERE status IN ('pending_replay', 'pending_missing_ancestors')
            """
        ).fetchone()
        consensus_counts = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM consensus_block_proposals
            GROUP BY status
            """
        ).fetchall()
        checkpoint = latest_checkpoint_in_connection(connection)
        active_base = active_snapshot_base_in_connection(connection)
        pre_snapshot_headers = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM network_block_headers
            WHERE status = 'skipped_pre_snapshot'
               OR (
                   status IN ('pending_replay', 'pending_missing_ancestors')
                   AND height <= ?
               )
            """,
            (int(active_base["height"]) if active_base is not None else 0,),
        ).fetchone()
    latest_height = int(latest_block["height"]) if latest_block else 0
    latest_hash = latest_block["block_hash"] if latest_block else GENESIS_HASH
    effective_height = latest_height
    effective_hash = latest_hash
    snapshot_height = int(active_base["height"]) if active_base is not None else 0
    snapshot_hash = active_base["block_hash"] if active_base is not None else None
    if active_base is not None and int(active_base.get("height") or 0) > effective_height:
        effective_height = int(active_base["height"])
        effective_hash = active_base["block_hash"]
    try:
        from app.services.consensus import get_replay_status

        replay_status = get_replay_status()
    except Exception as exc:
        replay_status = {"active": False, "error": str(exc)}
    return {
        **node_identity(),
        "latest_block_height": latest_height,
        "latest_block_hash": latest_hash,
        "local_block_height": latest_height,
        "local_block_hash": latest_hash,
        "snapshot_height": snapshot_height,
        "snapshot_hash": snapshot_hash,
        "effective_latest_block_height": effective_height,
        "effective_latest_block_hash": effective_hash,
        "catch_up_start_height": effective_height,
        "latest_checkpoint": checkpoint,
        "active_snapshot_base": active_base,
        "peer_counts": dict(peer_counts) if peer_counts else {"total": 0, "connected": 0, "stale": 0},
        "mempool": {row["status"]: row["count"] for row in mempool_counts},
        "pending_replay_blocks": pending_headers["count"] if pending_headers else 0,
        "headers_skipped_pre_snapshot": pre_snapshot_headers["count"] if pre_snapshot_headers else 0,
        "replay": replay_status,
        "sync_status": replay_status.get("sync_status", "healthy"),
        "replay_stalled": bool(replay_status.get("replay_stalled")),
        "replay_last_progress_at": replay_status.get("replay_last_progress_at"),
        "replay_last_imported_height": replay_status.get("replay_last_imported_height"),
        "replay_consecutive_failures": int(replay_status.get("replay_consecutive_failures") or 0),
        "divergence_detected": bool(replay_status.get("divergence_detected")),
        "divergence_reason": replay_status.get("divergence_reason"),
        "auto_recovery_active": bool(replay_status.get("auto_recovery_active")),
        "consensus": {row["status"]: row["count"] for row in consensus_counts},
        "sync_mode": "proposal_vote_finalize_replay_alpha",
        "checked_at": _now(),
    }


def get_blocks_since(from_height: int, limit: int = 100) -> dict[str, Any]:
    if from_height < 0:
        raise NetworkError(422, "from_height must be >= 0")
    blocks = []
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT height, previous_hash, miner_id, range_start, range_end, algorithm,
                   result_hash, merkle_root, samples, timestamp, block_hash, reward,
                   tx_merkle_root, tx_count, tx_hashes, fee_reward, miner_reward_address, state_root,
                   difficulty, task_id, protocol_params_id, protocol_version,
                   validation_mode, total_task_ms, total_block_ms,
                   fraudulent, fraud_reason, fraud_detected_at
            FROM blocks
            WHERE height > ?
            ORDER BY height ASC
            LIMIT ?
            """,
            (from_height, limit),
        ).fetchall()
        for row in rows:
            block = row_to_dict(row) or {}
            block["samples"] = _decode_json(block.get("samples"), [])
            block["tx_hashes"] = _decode_json(block.get("tx_hashes"), [])
            block["tx_count"] = int(block.get("tx_count") or 0)
            block["fee_reward"] = round(float(block.get("fee_reward") or 0), 8)
            block["fraudulent"] = bool(block.get("fraudulent"))
            validator_rows = connection.execute(
                """
                SELECT account_id, account_type, amount, related_id
                FROM ledger_entries
                WHERE block_height = ? AND entry_type = 'validator_reward'
                ORDER BY id ASC
                """,
                (block["height"],),
            ).fetchall()
            validator_rows = [row_to_dict(item) for item in validator_rows]
            related_id = next((str(item.get("related_id") or "") for item in validator_rows if item.get("related_id")), "")
            reward_validator_ids = _validator_reward_ids_for_related_id(connection, related_id, len(validator_rows))
            validator_ids = []
            reward_addresses: dict[str, str] = {}
            for index, item in enumerate(validator_rows):
                validator_id = (
                    reward_validator_ids[index]
                    if index < len(reward_validator_ids)
                    else str(item["account_id"])
                )
                validator_ids.append(validator_id)
                if str(item.get("account_type") or "") == "wallet" or is_valid_address(str(item["account_id"])):
                    reward_addresses[validator_id] = str(item["account_id"])
            validator_pool = round(sum(float(item["amount"]) for item in validator_rows), 8)
            block["validator_reward"] = {
                "pool": validator_pool,
                "per_validator": round(validator_pool / len(validator_ids), 8) if validator_ids else 0.0,
                "validator_ids": validator_ids,
                "reward_addresses": reward_addresses,
            }
            tx_rows = connection.execute(
                """
                SELECT *
                FROM mempool_transactions
                WHERE block_height = ?
                ORDER BY created_at ASC
                """,
                (block["height"],),
            ).fetchall()
            block["transactions"] = [_decode_tx(row_to_dict(tx_row)) for tx_row in tx_rows]
            blocks.append(block)
    return {"from_height": from_height, "count": len(blocks), "blocks": blocks}


def _validator_reward_ids_for_related_id(connection: Any, related_id: str, limit: int) -> list[str]:
    if not related_id or limit <= 0:
        return []
    rows = connection.execute(
        """
        SELECT validator_id
        FROM validation_votes
        WHERE job_id = ? AND approved = 1
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (related_id, limit),
    ).fetchall()
    validator_ids = [str(row["validator_id"]) for row in rows]
    if validator_ids:
        return validator_ids
    rows = connection.execute(
        """
        SELECT validator_id
        FROM consensus_votes
        WHERE proposal_id = ? AND approved = 1
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (related_id, limit),
    ).fetchall()
    return [str(row["validator_id"]) for row in rows]


def receive_block_header(block: dict[str, Any], source_peer_id: str | None = None) -> dict[str, Any]:
    required = {"height", "previous_hash", "block_hash", "timestamp"}
    missing = sorted(required - set(block))
    if missing:
        raise NetworkError(422, f"missing block fields: {', '.join(missing)}")
    if int(block["height"]) < 1:
        raise NetworkError(422, "block height must be positive")
    if not isinstance(block["block_hash"], str) or len(block["block_hash"]) != 64:
        raise NetworkError(422, "invalid block_hash")
    if not isinstance(block["previous_hash"], str) or len(block["previous_hash"]) != 64:
        raise NetworkError(422, "invalid previous_hash")

    status = "pending_replay"
    reason = "accepted for distributed replay queue"
    with get_connection() as connection:
        local = connection.execute("SELECT block_hash FROM blocks WHERE height = ?", (int(block["height"]),)).fetchone()
        queued = connection.execute(
            "SELECT status, reason FROM network_block_headers WHERE block_hash = ?",
            (block["block_hash"],),
        ).fetchone()
        latest = connection.execute(
            "SELECT height, block_hash FROM blocks ORDER BY height DESC LIMIT 1"
        ).fetchone()
        latest_height = int(latest["height"]) if latest is not None else 0
        latest_hash = latest["block_hash"] if latest is not None else GENESIS_HASH
        active_base = active_snapshot_base_in_connection(connection)
        active_base_height = int(active_base["height"]) if active_base is not None else 0
        if local is not None and local["block_hash"] == block["block_hash"]:
            status = "known"
            reason = "block already known locally"
        elif queued is not None:
            _record_sync_event(
                connection,
                source_peer_id,
                "block_received",
                "inbound",
                "known",
                {
                    "height": int(block["height"]),
                    "block_hash": block["block_hash"],
                    "reason": "block header already queued",
                    "queued_status": queued["status"],
                },
            )
            return {
                "accepted": True,
                "status": "known",
                "reason": "block header already queued",
                "block_hash": block["block_hash"],
            }
        elif local is not None and local["block_hash"] != block["block_hash"]:
            raise NetworkError(409, "conflicting block at height")
        elif active_base is not None and int(block["height"]) <= active_base_height:
            status = "skipped_pre_snapshot"
            reason = "block covered by active snapshot base"
        elif int(block["height"]) == latest_height + 1 and block["previous_hash"] != latest_hash:
            continues_snapshot = (
                active_base is not None
                and latest_height == 0
                and int(block["height"]) == int(active_base["height"]) + 1
                and block["previous_hash"] == active_base["block_hash"]
            )
            if continues_snapshot:
                status = "pending_replay"
                reason = "accepted after active snapshot base"
            else:
                status = "pending_missing_ancestors"
                reason = "accepted but local chain tip does not match previous_hash"
        elif int(block["height"]) > latest_height + 1:
            if (
                active_base is not None
                and latest_height == 0
                and int(block["height"]) == int(active_base["height"]) + 1
                and block["previous_hash"] == active_base["block_hash"]
            ):
                status = "pending_replay"
                reason = "accepted after active snapshot base"
            elif active_base is not None and int(block["height"]) > int(active_base["height"]) + 1:
                status = "pending_missing_ancestors"
                reason = "accepted after snapshot base but missing intermediate blocks"
            else:
                status = "pending_missing_ancestors"
                reason = "accepted but missing ancestor blocks"
        connection.execute(
            """
            INSERT INTO network_block_headers (
                block_hash, height, previous_hash, source_peer_id, status, reason, payload, received_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(block_hash) DO UPDATE SET
                source_peer_id = excluded.source_peer_id,
                status = excluded.status,
                reason = excluded.reason,
                payload = excluded.payload,
                received_at = excluded.received_at
            """,
            (
                block["block_hash"],
                int(block["height"]),
                block["previous_hash"],
                source_peer_id,
                status,
                reason,
                json.dumps(block, sort_keys=True),
                _now(),
            ),
        )
        _record_sync_event(
            connection,
            source_peer_id,
            "block_received",
            "inbound",
            status,
            {"height": int(block["height"]), "block_hash": block["block_hash"], "reason": reason},
        )
    return {"accepted": True, "status": status, "reason": reason, "block_hash": block["block_hash"]}


def submit_transaction(tx: dict[str, Any], propagated: bool = False) -> dict[str, Any]:
    tx_hash = tx.get("tx_hash", "unknown")
    sender = tx.get("sender", "unknown")
    nonce = tx.get("nonce", "unknown")
    recipient = tx.get("recipient", "unknown")
    network_id = tx.get("network_id", "unknown")
    chain_id = tx.get("chain_id", "unknown")
    
    logger.info(
        f"[TX_SUBMIT] Received transaction: tx_hash={tx_hash}, sender={sender}, "
        f"recipient={recipient}, nonce={nonce}, network_id={network_id}, chain_id={chain_id}, "
        f"propagated={propagated}"
    )
    
    try:
        _validate_signed_transaction(tx)
        logger.debug(f"[TX_SUBMIT] Validation passed for tx {tx_hash}")
    except NetworkError as ve:
        logger.warning(f"[TX_SUBMIT] Validation failed for tx {tx_hash}: {ve.detail} (code={ve.status_code})")
        raise
    
    timestamp = _now()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=MEMPOOL_TX_TTL_SECONDS)).isoformat()
    payload_json = json.dumps(_unsigned_from_tx(tx), sort_keys=True)
    inserted = False
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT tx_hash, status FROM mempool_transactions WHERE tx_hash = ?",
            (tx["tx_hash"],),
        ).fetchone()
        if existing is not None:
            if existing["status"] in TERMINAL_TX_STATUSES:
                logger.info(f"[TX_SUBMIT] Duplicate tx {tx_hash}: already {existing['status']}")
                raise NetworkError(409, f"transaction already {existing['status']}")
            logger.debug(f"[TX_SUBMIT] Tx {tx_hash} already pending, returning existing")
            return get_transaction(tx["tx_hash"]) or {}
        
        nonce_conflict = connection.execute(
            """
            SELECT tx_hash
            FROM mempool_transactions
            WHERE sender = ? AND nonce = ? AND tx_hash <> ? AND status NOT IN ('rejected', 'failed', 'expired')
            """,
            (tx["sender"], int(tx["nonce"]), tx["tx_hash"]),
        ).fetchone()
        if nonce_conflict is not None:
            logger.warning(f"[TX_SUBMIT] Nonce conflict for {sender} nonce {nonce}: existing tx {nonce_conflict['tx_hash']}")
            raise NetworkError(409, "duplicate sender nonce")
        
        connection.execute(
            """
            DELETE FROM mempool_transactions
            WHERE sender = ? AND nonce = ? AND status IN ('rejected', 'failed', 'expired')
            """,
            (tx["sender"], int(tx["nonce"])),
        )
        connection.execute(
            """
            INSERT INTO mempool_transactions (
                tx_hash, tx_type, sender, recipient, amount, amount_units, nonce, fee, fee_units,
                payload, public_key, signature, status, propagated,
                block_height, rejection_reason, expires_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, NULL, ?, ?, ?)
            """,
            (
                tx["tx_hash"],
                tx["tx_type"],
                tx["sender"],
                tx.get("recipient"),
                units_to_float(_tx_amount_units(tx)),
                _tx_amount_units(tx),
                int(tx["nonce"]),
                units_to_float(_tx_fee_units(tx)),
                _tx_fee_units(tx),
                payload_json,
                tx["public_key"],
                tx["signature"],
                1 if propagated else 0,
                expires_at,
                timestamp,
                timestamp,
            ),
        )
        inserted = True
        logger.info(f"[TX_SUBMIT] Transaction {tx_hash} inserted into mempool (propagated={propagated})")
        _record_sync_event(connection, None, "transaction_received", "inbound", "accepted", {"tx_hash": tx["tx_hash"]})
    
    accepted = get_transaction(tx["tx_hash"]) or {}
    if inserted and not propagated:
        logger.debug(f"[TX_SUBMIT] Gossiping transaction {tx_hash} to peers")
        gossip_result = gossip_json("/tx/receive", tx, "tx_gossip")
        try:
            succeeded = int(gossip_result.get("succeeded", 0))
        except Exception:
            succeeded = 0
        if succeeded > 0:
            try:
                with get_connection() as conn2:
                    result = conn2.execute(
                        "UPDATE mempool_transactions SET propagated = 1, updated_at = ? WHERE tx_hash = ?",
                        (timestamp, tx_hash),
                    )
                    if result.rowcount:
                        logger.info(f"[TX_PROPAGATED_DB_UPDATED] Marked transaction {tx_hash} propagated after gossip to {succeeded} peers")
                    else:
                        logger.warning(f"[TX_PROPAGATED_DB_FAILED] No matching mempool row found for {tx_hash} while marking propagated")
            except Exception as exc:
                logger.error(f"[TX_PROPAGATED_COMMIT_FAILED] Failed to persist propagated flag for {tx_hash}: {exc}")
        else:
            logger.debug(f"[TX_PROPAGATED] Transaction {tx_hash} gossip sent but no peers accepted it yet")

    return accepted


def gossip_json(
    path: str,
    payload: dict[str, Any],
    event_type: str,
    exclude_peer_id: str | None = None,
) -> dict[str, Any]:
    if not GOSSIP_ENABLED:
        return {"enabled": False, "attempted": 0, "succeeded": 0, "failed": 0, "peers": []}
    peers = [peer for peer in list_peers(include_stale=False) if peer["peer_id"] != exclude_peer_id]
    peers = peers[:GOSSIP_MAX_PEERS]
    attempted = 0
    succeeded = 0
    failed = 0
    results: list[dict[str, Any]] = []
    for peer in peers:
        attempted += 1
        peer_address = _normalize_peer_address(peer.get("peer_address"))
        if not peer_address:
            failed += 1
            status = "failed"
            detail = {"url": peer.get("peer_address"), "error": "invalid peer_address"}
            results.append({"peer_id": peer["peer_id"], **detail})
            with get_connection() as connection:
                _record_sync_event(connection, peer["peer_id"], event_type, "outbound", status, detail)
            continue
        url = f"{peer_address}{path}"
        try:
            response = requests.post(url, json=payload, timeout=GOSSIP_TIMEOUT_SECONDS)
            ok = 200 <= response.status_code < 300
            succeeded += 1 if ok else 0
            failed += 0 if ok else 1
            status = "accepted" if ok else "failed"
            detail = {"url": url, "status_code": response.status_code}
        except requests.RequestException as exc:
            failed += 1
            status = "failed"
            detail = {"url": url, "error": str(exc)}
        results.append({"peer_id": peer["peer_id"], **detail})
        with get_connection() as connection:
            _record_sync_event(connection, peer["peer_id"], event_type, "outbound", status, detail)
    return {
        "enabled": True,
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "peers": results,
    }


def reconcile_peer(peer_address: str) -> dict[str, Any]:
    peer_address = peer_address.rstrip("/")
    result = {
        "peer_address": peer_address,
        "identity_registered": False,
        "peers_seen": 0,
        "mempool_inventory_seen": 0,
        "mempool_inventory_missing": 0,
        "transactions_seen": 0,
        "transactions_imported": 0,
        "proposals_seen": 0,
        "proposals_imported": 0,
        "blocks_seen": 0,
        "blocks_imported": 0,
        "sync_from_height": 0,
        "catch_up_start_height": 0,
        "local_block_height": 0,
        "snapshot_height": 0,
        "effective_latest_block_height": 0,
        "replay": {},
        "headers_skipped_pre_snapshot": 0,
        "errors": [],
    }
    try:
        identity = requests.get(f"{peer_address}/node/identity", timeout=GOSSIP_TIMEOUT_SECONDS).json()
        register_peer(
            node_id=identity["node_id"],
            peer_address=identity["peer_address"],
            peer_type=identity["peer_type"],
            protocol_version=identity["protocol_version"],
            network_id=identity["network_id"],
            chain_id=identity["chain_id"],
            genesis_hash=identity["genesis_hash"],
            metadata={"source": "reconcile"},
        )
        result["identity_registered"] = True
    except Exception as exc:
        result["errors"].append(f"identity: {exc}")

    try:
        peer_rows = requests.get(f"{peer_address}/node/peers", timeout=GOSSIP_TIMEOUT_SECONDS).json()
        for peer in peer_rows:
            result["peers_seen"] += 1
            try:
                register_peer(
                    node_id=peer["node_id"],
                    peer_address=peer["peer_address"],
                    peer_type=peer["peer_type"],
                    protocol_version=peer["protocol_version"],
                    network_id=peer["network_id"],
                    chain_id=peer["chain_id"],
                    genesis_hash=peer["genesis_hash"],
                    metadata={"source": "peer_reconcile"},
                )
            except Exception as exc:
                result["errors"].append(f"peer {peer.get('peer_address')}: {exc}")
    except Exception as exc:
        result["errors"].append(f"peers: {exc}")

    try:
        tx_rows = _fetch_peer_mempool_transactions(peer_address, result, limit=100)
        for tx in tx_rows:
            result["transactions_seen"] += 1
            try:
                submit_transaction(tx, propagated=True)
                result["transactions_imported"] += 1
            except Exception as exc:
                result["errors"].append(f"tx {tx.get('tx_hash')}: {exc}")
    except Exception as exc:
        result["errors"].append(f"mempool: {exc}")

    try:
        block_sync = sync_blocks_until(peer_address, limit=100)
        result["sync_from_height"] = block_sync["sync_from_height"]
        result["catch_up_start_height"] = block_sync["catch_up_start_height"]
        result["local_block_height"] = block_sync["local_block_height"]
        result["snapshot_height"] = block_sync["snapshot_height"]
        result["effective_latest_block_height"] = block_sync["effective_latest_block_height"]
        result["replay"] = block_sync.get("replay", {})
        result["headers_skipped_pre_snapshot"] = int(
            result["replay"].get("headers_skipped_pre_snapshot") or 0
        )
        result["blocks_seen"] += block_sync["blocks_seen"]
        result["blocks_imported"] += block_sync["blocks_imported"]
        result["errors"].extend(block_sync["errors"])
    except Exception as exc:
        result["errors"].append(f"blocks: {exc}")

    try:
        from app.services.consensus import propose_block

        proposals = requests.get(f"{peer_address}/consensus/proposals?limit=100", timeout=GOSSIP_TIMEOUT_SECONDS).json()
        for proposal in proposals:
            result["proposals_seen"] += 1
            payload = proposal.get("payload") or {}
            if int(payload.get("height") or 0) <= int(result["sync_from_height"]):
                continue
            try:
                propose_block(payload, proposal["proposer_node_id"], gossip=False)
                result["proposals_imported"] += 1
            except Exception as exc:
                result["errors"].append(f"proposal {proposal.get('proposal_id')}: {exc}")
    except Exception as exc:
        result["errors"].append(f"proposals: {exc}")

    with get_connection() as connection:
        _record_sync_event(connection, None, "peer_reconcile", "outbound", "completed", result)
    return result


def _fetch_peer_mempool_transactions(
    peer_address: str,
    result: dict[str, Any],
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    try:
        inventory_response = requests.get(
            f"{peer_address}/mempool/inventory?status=pending&limit={int(limit)}",
            timeout=GOSSIP_TIMEOUT_SECONDS,
        )
        inventory_response.raise_for_status()
        inventory_payload = inventory_response.json()
        inventory_rows = (
            inventory_payload.get("transactions", [])
            if isinstance(inventory_payload, dict)
            else inventory_payload
        )
        result["mempool_inventory_seen"] = len(inventory_rows)
        tx_hashes = [
            str(item.get("tx_hash") or "")
            for item in inventory_rows
            if isinstance(item, dict) and isinstance(item.get("tx_hash"), str) and len(str(item.get("tx_hash"))) == 64
        ]
        missing_hashes = _missing_mempool_tx_hashes(tx_hashes)
        result["mempool_inventory_missing"] = len(missing_hashes)
        rows: list[dict[str, Any]] = []
        for tx_hash in missing_hashes:
            tx_response = requests.get(f"{peer_address}/tx/{tx_hash}", timeout=GOSSIP_TIMEOUT_SECONDS)
            tx_response.raise_for_status()
            rows.append(tx_response.json())
        return rows
    except Exception as inventory_exc:
        result["mempool_inventory_error"] = str(inventory_exc)
        try:
            return requests.get(f"{peer_address}/mempool?limit={int(limit)}", timeout=GOSSIP_TIMEOUT_SECONDS).json()
        except Exception as fallback_exc:
            raise NetworkError(
                502,
                f"mempool inventory failed: {inventory_exc}; mempool fallback failed: {fallback_exc}",
            ) from fallback_exc


def _missing_mempool_tx_hashes(tx_hashes: list[str]) -> list[str]:
    unique_hashes = list(dict.fromkeys(tx_hashes))
    if not unique_hashes:
        return []
    placeholders = ",".join("?" for _ in unique_hashes)
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT tx_hash FROM mempool_transactions WHERE tx_hash IN ({placeholders})",
            tuple(unique_hashes),
        ).fetchall()
    existing = {str(row["tx_hash"]) for row in rows}
    return [tx_hash for tx_hash in unique_hashes if tx_hash not in existing]


def sync_blocks_until(
    peer_address: str,
    *,
    target_height: int | None = None,
    from_height: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    peer_address = peer_address.rstrip("/")
    result = {
        "peer_address": peer_address,
        "sync_from_height": 0,
        "local_block_height": 0,
        "snapshot_height": 0,
        "effective_latest_block_height": 0,
        "catch_up_start_height": 0,
        "target_height": target_height,
        "blocks_seen": 0,
        "blocks_imported": 0,
        "replay": {},
        "errors": [],
    }
    with get_connection() as connection:
        latest = connection.execute("SELECT COALESCE(MAX(height), 0) AS height FROM blocks").fetchone()
        latest_height = int(latest["height"] if latest else 0)
        active_base = active_snapshot_base_in_connection(connection)
        base_height = int(active_base["height"]) if active_base is not None else 0
        effective_height = max(latest_height, base_height)
        sync_from_height = effective_height if from_height is None else max(int(from_height), base_height)
    result["local_block_height"] = latest_height
    result["snapshot_height"] = base_height
    result["effective_latest_block_height"] = effective_height
    result["sync_from_height"] = sync_from_height
    result["catch_up_start_height"] = sync_from_height

    rounds = 0
    max_rounds = 20
    while rounds < max_rounds:
        rounds += 1
        request_limit = max(1, min(int(limit), 100))
        if target_height is not None:
            if sync_from_height >= int(target_height):
                break
            request_limit = max(1, min(request_limit, int(target_height) - sync_from_height))
        block_rows = requests.get(
            f"{peer_address}/node/sync/blocks?from_height={sync_from_height}&limit={request_limit}",
            timeout=GOSSIP_TIMEOUT_SECONDS,
        ).json()
        blocks = sorted(block_rows.get("blocks", []), key=lambda item: int(item.get("height") or 0))
        if not blocks:
            break
        max_seen_height = sync_from_height
        for block in blocks:
            result["blocks_seen"] += 1
            max_seen_height = max(max_seen_height, int(block.get("height") or 0))
            try:
                receive_block_header(block, source_peer_id=None)
                result["blocks_imported"] += 1
            except Exception as exc:
                result["errors"].append(f"block {block.get('height')}: {exc}")
        if max_seen_height <= sync_from_height:
            break
        sync_from_height = max_seen_height
        if len(blocks) < request_limit:
            break

    try:
        from app.services.consensus import get_replay_status, replay_finalized_blocks

        replay_status = get_replay_status()
        if bool(replay_status.get("active")):
            result["replay"] = {"status": "skipped", "reason": "replay already active", **replay_status}
        elif replay_status.get("sync_status") == "divergent":
            result["replay"] = {"status": "skipped", "reason": "replay divergent; restore required", **replay_status}
            if AUTO_RECOVERY_ENABLED:
                result["auto_recovery"] = recover_from_peer_snapshot(peer_address, source="auto-recovery")
        elif int(replay_status.get("queue_size") or 0) > REPLAY_BACKLOG_THRESHOLD:
            result["replay"] = replay_finalized_blocks(REPLAY_BATCH_SIZE)
            result["replay"]["reason"] = "replay backlog drained with bounded batch"
        else:
            result["replay"] = replay_finalized_blocks(min(max(int(limit), 1), REPLAY_BATCH_SIZE))
        with get_connection() as connection:
            latest = connection.execute("SELECT COALESCE(MAX(height), 0) AS height FROM blocks").fetchone()
            latest_height = int(latest["height"] if latest else 0)
            active_base = active_snapshot_base_in_connection(connection)
            base_height = int(active_base["height"]) if active_base is not None else 0
            result["effective_latest_block_height"] = max(latest_height, base_height)
    except Exception as exc:
        result["errors"].append(f"replay: {exc}")
    return result


def reconcile_connected_peers(limit: int = 16) -> dict[str, Any]:
    peers = select_reconcile_peers(limit)
    results = [reconcile_peer(peer["peer_address"]) for peer in peers]
    return {
        "attempted": len(results),
        "selected_peers": [
            {
                "peer_id": peer["peer_id"],
                "node_id": peer["node_id"],
                "peer_address": peer["peer_address"],
                "peer_type": peer["peer_type"],
            }
            for peer in peers
        ],
        "transactions_imported": sum(item["transactions_imported"] for item in results),
        "proposals_imported": sum(item["proposals_imported"] for item in results),
        "blocks_imported": sum(item["blocks_imported"] for item in results),
        "peers_seen": sum(item["peers_seen"] for item in results),
        "errors": sum(len(item["errors"]) for item in results),
        "results": results,
    }


def get_transaction(tx_hash: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM mempool_transactions WHERE tx_hash = ?", (tx_hash,)).fetchone()
    if row is None:
        return None
    return _decode_tx(row_to_dict(row))


def list_mempool(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    expire_mempool_transactions()
    query = "SELECT * FROM mempool_transactions"
    params: tuple[Any, ...]
    if status:
        query += " WHERE status = ?"
        params = (status,)
    else:
        params = ()
    # Return newest transactions first so explorers show recent activity
    query += " ORDER BY created_at DESC LIMIT ?"
    params = (*params, limit)
    with get_connection() as connection:
        return [_decode_tx(row_to_dict(row)) for row in connection.execute(query, params).fetchall()]


def list_mempool_inventory(status: str | None = "pending", limit: int = 100) -> dict[str, Any]:
    expire_mempool_transactions()
    query = """
        SELECT tx_hash, status, sender, recipient, nonce, fee_units, updated_at, created_at
        FROM mempool_transactions
    """
    params: tuple[Any, ...]
    if status:
        query += " WHERE status = ?"
        params = (status,)
    else:
        params = ()
    query += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
    params = (*params, limit)
    with get_connection() as connection:
        rows = [row_to_dict(row) for row in connection.execute(query, params).fetchall()]
    return {
        "status": status,
        "count": len(rows),
        "transactions": rows,
        "checked_at": _now(),
    }


def list_recent_transactions(status: str | None = None, address: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    expire_mempool_transactions()
    query = "SELECT * FROM mempool_transactions"
    filters: list[str] = []
    params: list[Any] = []
    if status:
        filters.append("status = ?")
        params.append(status)
    if address:
        filters.append("(sender = ? OR recipient = ?)")
        params.extend([address, address])
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY COALESCE(confirmed_at, selected_at, created_at) DESC LIMIT ?"
    params.append(limit)
    with get_connection() as connection:
        return [_decode_tx(row_to_dict(row)) for row in connection.execute(query, tuple(params)).fetchall()]


def expire_mempool_transactions() -> int:
    timestamp = _now()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE mempool_transactions
            SET status = 'expired', rejection_reason = 'ttl expired', failure_reason = 'ttl expired', updated_at = ?
            WHERE status IN ('pending', 'propagated', 'released') AND expires_at < ?
            """,
            (timestamp, timestamp),
        )
        return int(cursor.rowcount)


def _validate_signed_transaction(tx: dict[str, Any]) -> None:
    required = {
        "tx_hash",
        "tx_type",
        "sender",
        "nonce",
        "fee",
        "timestamp",
        "payload",
        "public_key",
        "signature",
        "network_id",
        "chain_id",
    }
    missing = sorted(required - set(tx))
    if missing:
        raise NetworkError(422, f"missing transaction fields: {', '.join(missing)}")
    
    if len(json.dumps(tx, sort_keys=True, separators=(",", ":")).encode("utf-8")) > MAX_TX_SIZE_BYTES:
        raise NetworkError(413, "transaction payload too large")
    
    if tx["tx_type"] not in ALLOWED_TX_TYPES:
        raise NetworkError(422, "unsupported transaction type")
    
    if int(tx["nonce"]) < 1:
        raise NetworkError(422, "nonce must be >= 1")
    
    if _tx_fee_units(tx) < 0 or units_to_float(_tx_fee_units(tx)) > MEMPOOL_MAX_FEE:
        raise NetworkError(422, "invalid fee")
    
    if _tx_fee_units(tx) < MIN_TX_FEE_UNITS:
        raise NetworkError(422, f"transaction fee below minimum ({MIN_TX_FEE_UNITS} units)")
    
    if _tx_amount_units(tx) < 0:
        raise NetworkError(422, "amount must be >= 0")
    
    if tx["tx_type"] == "transfer" and not is_valid_address(tx.get("recipient")):
        raise NetworkError(422, "transfer transaction requires a valid PI recipient")
    
    if not address_matches_public_key(tx["sender"], tx["public_key"]):
        raise NetworkError(401, "sender address does not match public key")
    
    # Most critical check: network/chain mismatch
    if tx["network_id"] != NETWORK_ID or str(tx["chain_id"]) != str(CHAIN_ID):
        logger.warning(
            f"[TX_VALIDATE] Network/chain mismatch: "
            f"tx network_id={tx['network_id']} (expected {NETWORK_ID}), "
            f"tx chain_id={tx['chain_id']} (expected {CHAIN_ID})"
        )
        raise NetworkError(409, f"transaction network or chain mismatch (tx: {tx['network_id']}/{tx['chain_id']}, node: {NETWORK_ID}/{CHAIN_ID})")
    
    unsigned_payload = _unsigned_from_tx(tx)
    signature_payload = matching_transaction_signature_payload(unsigned_payload, tx["public_key"], tx["tx_hash"])
    if signature_payload is None:
        raise NetworkError(401, "transaction hash mismatch")
    
    if not verify_payload_signature(tx["public_key"], signature_payload, tx["signature"]):
        raise NetworkError(401, "invalid transaction signature")


def _unsigned_from_tx(tx: dict[str, Any]) -> dict[str, Any]:
    return unsigned_transaction_payload(
        tx_type=tx["tx_type"],
        sender=tx["sender"],
        recipient=tx.get("recipient"),
        amount=canonical_amount(_tx_amount_units(tx)),
        nonce=int(tx["nonce"]),
        fee=canonical_amount(_tx_fee_units(tx)),
        payload=tx.get("payload") or {},
        timestamp=tx["timestamp"],
        network_id=tx.get("network_id", NETWORK_ID),
        chain_id=tx.get("chain_id", CHAIN_ID),
    )


def _tx_amount_units(tx: dict[str, Any]) -> int:
    if tx.get("amount_units") is not None:
        return int(tx.get("amount_units") or 0)
    return to_units(tx.get("amount", 0))


def _tx_fee_units(tx: dict[str, Any]) -> int:
    if tx.get("fee_units") is not None:
        return int(tx.get("fee_units") or 0)
    return to_units(tx.get("fee", 0))


def _decode_peer(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    row["metadata"] = _decode_json(row.get("metadata"), {})
    return row


def _decode_tx(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    unsigned_payload = _decode_json(row.get("payload"), {})
    return {
        **row,
        **unsigned_payload,
        "payload": unsigned_payload.get("payload", {}),
        "propagated": bool(row.get("propagated")),
    }


def _mark_stale_peers(*, force: bool = False) -> dict[str, Any]:
    global _PEER_STALE_MARK_LAST_RUN_MONOTONIC
    monotonic_now = time.monotonic()
    if not force and (
        monotonic_now - _PEER_STALE_MARK_LAST_RUN_MONOTONIC
    ) < PEER_STALE_MARK_MIN_INTERVAL_SECONDS:
        return {"updated": 0, "checked_at": _now(), "skipped": "throttled"}
    if not _PEER_STALE_MARK_LOCK.acquire(blocking=False):
        return {"updated": 0, "checked_at": _now(), "skipped": "already_running"}
    stale_before = (datetime.now(timezone.utc) - timedelta(seconds=PEER_TIMEOUT_SECONDS)).isoformat()
    timestamp = _now()
    updated = 0
    try:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE network_peers
                SET status = 'stale'
                WHERE status = 'connected' AND last_seen < ?
                """,
                (stale_before,),
            )
            updated += max(0, int(cursor.rowcount or 0))
            cursor = connection.execute(
                """
                UPDATE network_peers
                SET status = 'connected'
                WHERE peer_address IN (
                    SELECT peer_address FROM network_peers
                    WHERE peer_address IN ({})
                )
                """.format(",".join("?" for _ in BOOTSTRAP_PEERS) or "''"),
                tuple(BOOTSTRAP_PEERS),
            )
            updated += max(0, int(cursor.rowcount or 0))
            if BOOTSTRAP_PEERS:
                for peer_address in BOOTSTRAP_PEERS:
                    peer_id = sha256_text(f"{CHAIN_ID}:{peer_address}")[:32]
                    cursor = connection.execute(
                        """
                        INSERT INTO network_peers (
                            peer_id, node_id, peer_address, peer_type, protocol_version,
                            network_id, chain_id, genesis_hash, connected_at, last_seen,
                            status, metadata
                        )
                        VALUES (?, 'bootstrap', ?, 'bootstrap', ?, ?, ?, ?, ?, ?, 'connected', '{}')
                        ON CONFLICT(peer_id) DO NOTHING
                        """,
                        (peer_id, peer_address, PROTOCOL_VERSION, NETWORK_ID, CHAIN_ID, GENESIS_HASH, timestamp, timestamp),
                    )
                    updated += max(0, int(cursor.rowcount or 0))
        _PEER_STALE_MARK_LAST_RUN_MONOTONIC = monotonic_now
        return {"updated": updated, "checked_at": timestamp}
    except sqlite3.OperationalError as exc:
        if "database is locked" in str(exc).lower():
            logger.warning("peer stale mark skipped: database is locked")
            return {"updated": updated, "checked_at": timestamp, "skipped": "database_locked"}
        raise
    finally:
        _PEER_STALE_MARK_LOCK.release()


def _record_sync_event(
    connection: Any,
    peer_id: str | None,
    event_type: str,
    direction: str,
    status: str,
    details: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO network_sync_events (peer_id, event_type, direction, status, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (peer_id, event_type, direction, status, json.dumps(details, sort_keys=True), _now()),
    )
