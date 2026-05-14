from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.core.crypto import sha256_text
from app.core.settings import (
    BOOTSTRAP_PEERS,
    CHAIN_ID,
    GENESIS_HASH,
    GOSSIP_ENABLED,
    GOSSIP_MAX_PEERS,
    GOSSIP_TIMEOUT_SECONDS,
    MEMPOOL_MAX_FEE,
    MEMPOOL_TX_TTL_SECONDS,
    NETWORK_ID,
    NODE_ID,
    NODE_PUBLIC_ADDRESS,
    NODE_TYPE,
    PEER_TIMEOUT_SECONDS,
    PROJECT_NAME,
    PROTOCOL_VERSION,
)
from app.core.signatures import verify_payload_signature
from app.db.database import get_connection, row_to_dict
from app.services.state import active_snapshot_base_in_connection, latest_checkpoint_in_connection
from app.services.wallet import address_from_public_key, is_valid_address, transaction_hash, unsigned_transaction_payload


class NetworkError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


ALLOWED_NODE_TYPES = {"full", "miner", "validator", "auditor", "bootstrap"}
ALLOWED_TX_TYPES = {"transfer", "stake", "unstake", "science_job_create", "governance_action", "treasury_claim"}
TERMINAL_TX_STATUSES = {"confirmed", "rejected", "expired"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


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
    chain_id: str = CHAIN_ID,
    genesis_hash: str = GENESIS_HASH,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    peer_type = peer_type.lower().strip()
    if peer_type not in ALLOWED_NODE_TYPES:
        raise NetworkError(422, "invalid peer_type")
    if network_id != NETWORK_ID:
        raise NetworkError(409, "peer network_id mismatch")
    if chain_id != CHAIN_ID:
        raise NetworkError(409, "peer chain_id mismatch")
    if genesis_hash != GENESIS_HASH:
        raise NetworkError(409, "peer genesis_hash mismatch")
    peer_address = peer_address.rstrip("/")
    if not node_id.strip() or not peer_address:
        raise NetworkError(422, "node_id and peer_address are required")

    timestamp = _now()
    peer_id = sha256_text(f"{chain_id}:{peer_address}")[:32]
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
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        _record_sync_event(connection, peer_id, "peer_registered", "inbound", "accepted", {"peer_address": peer_address})
        peer = row_to_dict(connection.execute("SELECT * FROM network_peers WHERE peer_id = ?", (peer_id,)).fetchone())
    return _decode_peer(peer)


def list_peers(include_stale: bool = True) -> list[dict[str, Any]]:
    _mark_stale_peers()
    query = "SELECT * FROM network_peers"
    params: tuple[Any, ...] = ()
    if not include_stale:
        query += " WHERE status = 'connected'"
    query += " ORDER BY last_seen DESC"
    with get_connection() as connection:
        return [_decode_peer(row_to_dict(row)) for row in connection.execute(query, params).fetchall()]


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
    latest_height = int(latest_block["height"]) if latest_block else 0
    latest_hash = latest_block["block_hash"] if latest_block else GENESIS_HASH
    effective_height = latest_height
    effective_hash = latest_hash
    if active_base is not None and int(active_base.get("height") or 0) > effective_height:
        effective_height = int(active_base["height"])
        effective_hash = active_base["block_hash"]
    return {
        **node_identity(),
        "latest_block_height": latest_height,
        "latest_block_hash": latest_hash,
        "effective_latest_block_height": effective_height,
        "effective_latest_block_hash": effective_hash,
        "latest_checkpoint": checkpoint,
        "active_snapshot_base": active_base,
        "peer_counts": dict(peer_counts) if peer_counts else {"total": 0, "connected": 0, "stale": 0},
        "mempool": {row["status"]: row["count"] for row in mempool_counts},
        "pending_replay_blocks": pending_headers["count"] if pending_headers else 0,
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
                   tx_merkle_root, tx_count, tx_hashes, fee_reward, state_root,
                   difficulty, task_id, protocol_params_id, protocol_version,
                   validation_mode, fraudulent, fraud_reason, fraud_detected_at
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
                SELECT account_id, amount
                FROM ledger_entries
                WHERE block_height = ? AND entry_type = 'validator_reward'
                ORDER BY id ASC
                """,
                (block["height"],),
            ).fetchall()
            validator_ids = [item["account_id"] for item in validator_rows]
            validator_pool = round(sum(float(item["amount"]) for item in validator_rows), 8)
            block["validator_reward"] = {
                "pool": validator_pool,
                "per_validator": round(validator_pool / len(validator_ids), 8) if validator_ids else 0.0,
                "validator_ids": validator_ids,
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
        latest = connection.execute(
            "SELECT height, block_hash FROM blocks ORDER BY height DESC LIMIT 1"
        ).fetchone()
        latest_height = int(latest["height"]) if latest is not None else 0
        latest_hash = latest["block_hash"] if latest is not None else GENESIS_HASH
        if local is not None and local["block_hash"] == block["block_hash"]:
            status = "known"
            reason = "block already known locally"
        elif local is not None and local["block_hash"] != block["block_hash"]:
            raise NetworkError(409, "conflicting block at height")
        elif int(block["height"]) == latest_height + 1 and block["previous_hash"] != latest_hash:
            active_base = active_snapshot_base_in_connection(connection)
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
                raise NetworkError(409, "previous_hash does not match local chain tip")
        elif int(block["height"]) > latest_height + 1:
            active_base = active_snapshot_base_in_connection(connection)
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
    _validate_signed_transaction(tx)
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
                raise NetworkError(409, f"transaction already {existing['status']}")
            return get_transaction(tx["tx_hash"]) or {}
        nonce_conflict = connection.execute(
            """
            SELECT tx_hash
            FROM mempool_transactions
            WHERE sender = ? AND nonce = ? AND tx_hash <> ? AND status NOT IN ('rejected', 'expired')
            """,
            (tx["sender"], int(tx["nonce"]), tx["tx_hash"]),
        ).fetchone()
        if nonce_conflict is not None:
            raise NetworkError(409, "duplicate sender nonce")
        connection.execute(
            """
            INSERT INTO mempool_transactions (
                tx_hash, tx_type, sender, recipient, amount, nonce, fee,
                payload, public_key, signature, status, propagated,
                block_height, rejection_reason, expires_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, NULL, ?, ?, ?)
            """,
            (
                tx["tx_hash"],
                tx["tx_type"],
                tx["sender"],
                tx.get("recipient"),
                float(tx.get("amount", 0)),
                int(tx["nonce"]),
                float(tx.get("fee", 0)),
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
        _record_sync_event(connection, None, "transaction_received", "inbound", "accepted", {"tx_hash": tx["tx_hash"]})
    accepted = get_transaction(tx["tx_hash"]) or {}
    if inserted and not propagated:
        gossip_json("/tx/receive", tx, "tx_gossip")
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
        url = f"{peer['peer_address'].rstrip('/')}{path}"
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
        "transactions_seen": 0,
        "transactions_imported": 0,
        "proposals_seen": 0,
        "proposals_imported": 0,
        "blocks_seen": 0,
        "blocks_imported": 0,
        "sync_from_height": 0,
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
        tx_rows = requests.get(f"{peer_address}/mempool?limit=100", timeout=GOSSIP_TIMEOUT_SECONDS).json()
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
        with get_connection() as connection:
            latest = connection.execute("SELECT COALESCE(MAX(height), 0) AS height FROM blocks").fetchone()
            latest_height = int(latest["height"] if latest else 0)
            active_base = active_snapshot_base_in_connection(connection)
            base_height = int(active_base["height"]) if active_base is not None else 0
            sync_from_height = max(latest_height, base_height)
        result["sync_from_height"] = sync_from_height
        block_rows = requests.get(
            f"{peer_address}/node/sync/blocks?from_height={sync_from_height}&limit=100",
            timeout=GOSSIP_TIMEOUT_SECONDS,
        ).json()
        for block in block_rows.get("blocks", []):
            result["blocks_seen"] += 1
            try:
                receive_block_header(block, source_peer_id=None)
                result["blocks_imported"] += 1
            except Exception as exc:
                result["errors"].append(f"block {block.get('height')}: {exc}")
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


def reconcile_connected_peers(limit: int = 16) -> dict[str, Any]:
    peers = list_peers(include_stale=False)[:limit]
    results = [reconcile_peer(peer["peer_address"]) for peer in peers]
    return {
        "attempted": len(results),
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
    query += " ORDER BY created_at ASC LIMIT ?"
    params = (*params, limit)
    with get_connection() as connection:
        return [_decode_tx(row_to_dict(row)) for row in connection.execute(query, params).fetchall()]


def expire_mempool_transactions() -> int:
    timestamp = _now()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE mempool_transactions
            SET status = 'expired', rejection_reason = 'ttl expired', updated_at = ?
            WHERE status IN ('pending', 'propagated') AND expires_at < ?
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
    if tx["tx_type"] not in ALLOWED_TX_TYPES:
        raise NetworkError(422, "unsupported transaction type")
    if int(tx["nonce"]) < 1:
        raise NetworkError(422, "nonce must be >= 1")
    if float(tx.get("fee", 0)) < 0 or float(tx.get("fee", 0)) > MEMPOOL_MAX_FEE:
        raise NetworkError(422, "invalid fee")
    if float(tx.get("amount", 0)) < 0:
        raise NetworkError(422, "amount must be >= 0")
    if tx["tx_type"] == "transfer" and not is_valid_address(tx.get("recipient")):
        raise NetworkError(422, "transfer transaction requires a valid PI recipient")
    if address_from_public_key(tx["public_key"]) != tx["sender"]:
        raise NetworkError(401, "sender address does not match public key")
    unsigned_payload = _unsigned_from_tx(tx)
    if tx["network_id"] != NETWORK_ID or tx["chain_id"] != CHAIN_ID:
        raise NetworkError(409, "transaction network or chain mismatch")
    if transaction_hash(unsigned_payload, tx["public_key"]) != tx["tx_hash"]:
        raise NetworkError(401, "transaction hash mismatch")
    if not verify_payload_signature(tx["public_key"], unsigned_payload, tx["signature"]):
        raise NetworkError(401, "invalid transaction signature")


def _unsigned_from_tx(tx: dict[str, Any]) -> dict[str, Any]:
    return unsigned_transaction_payload(
        tx_type=tx["tx_type"],
        sender=tx["sender"],
        recipient=tx.get("recipient"),
        amount=float(tx.get("amount", 0)),
        nonce=int(tx["nonce"]),
        fee=float(tx.get("fee", 0)),
        payload=tx.get("payload") or {},
        timestamp=tx["timestamp"],
        network_id=tx.get("network_id", NETWORK_ID),
        chain_id=tx.get("chain_id", CHAIN_ID),
    )


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


def _mark_stale_peers() -> None:
    stale_before = (datetime.now(timezone.utc) - timedelta(seconds=PEER_TIMEOUT_SECONDS)).isoformat()
    timestamp = _now()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE network_peers
            SET status = 'stale'
            WHERE status = 'connected' AND last_seen < ?
            """,
            (stale_before,),
        )
        connection.execute(
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
        if BOOTSTRAP_PEERS:
            for peer_address in BOOTSTRAP_PEERS:
                peer_id = sha256_text(f"{CHAIN_ID}:{peer_address}")[:32]
                connection.execute(
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
