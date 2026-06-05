from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routes import router
from app.core.settings import GENESIS_HASH
from app.core.signatures import generate_keypair
from app.db.database import DATABASE_PATH, get_connection, init_db
from app.services.network import node_identity
from app.services.mining import register_miner
from app.services.wallet import create_wallet


def _build_test_client(tmp_path, monkeypatch) -> TestClient:
    db_path = tmp_path / "test_api_endpoints.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    init_db(db_path)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_protocol_endpoint_returns_retarget_source_window_int(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)
    with get_connection() as connection:
        connection.execute(
            "UPDATE protocol_params SET retarget_source_window = 20, retarget_reason = 'test' WHERE active = 1"
        )
    response = client.get("/protocol")

    assert response.status_code == 200
    assert response.json()["retarget_source_window"] == 20


def test_node_sync_status_endpoint_returns_200(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)
    response = client.get("/node/sync-status")

    assert response.status_code == 200


def test_mempool_inventory_returns_pending_hashes_without_full_payload(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO mempool_transactions (
                tx_hash, tx_type, sender, recipient, amount, amount_units, nonce, fee, fee_units,
                payload, public_key, signature, status, propagated,
                expires_at, created_at, updated_at
            )
            VALUES (?, 'transfer', 'sender-a', 'recipient-a', 0, 0, 1, 0, 10,
                '{}', 'ed25519:public', 'ed25519:signature', 'pending', 0,
                '2099-01-01T00:00:00+00:00', '2026-06-04T00:00:00+00:00', '2026-06-04T00:00:00+00:00')
            """,
            ("e" * 64,),
        )

    response = client.get("/mempool/inventory")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["count"] == 1
    assert payload["transactions"][0]["tx_hash"] == "e" * 64
    assert "payload" not in payload["transactions"][0]
    assert "signature" not in payload["transactions"][0]


def test_node_blocks_receive_gossips_new_pending_block(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)
    calls: list[dict] = []

    def fake_gossip_json(path, payload, event_type, exclude_peer_id=None):
        calls.append(
            {
                "path": path,
                "payload": payload,
                "event_type": event_type,
                "exclude_peer_id": exclude_peer_id,
            }
        )
        return {"enabled": True, "attempted": 1, "succeeded": 1, "failed": 0, "peers": []}

    monkeypatch.setattr("app.api.routes.gossip_json", fake_gossip_json)
    block = {
        "height": 1,
        "previous_hash": GENESIS_HASH,
        "block_hash": "c" * 64,
        "timestamp": "2026-06-04T00:00:00+00:00",
    }

    response = client.post(
        "/node/blocks/receive",
        json={"block": block, "source_peer_id": "peer-a"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending_replay"
    assert payload["gossip"]["succeeded"] == 1
    assert calls == [
        {
            "path": "/node/blocks/receive?gossip=false",
            "payload": {"block": block, "source_peer_id": node_identity()["peer_id"]},
            "event_type": "block_payload_gossip",
            "exclude_peer_id": "peer-a",
        }
    ]


def test_node_blocks_receive_does_not_regossip_duplicate_header(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)
    calls: list[dict] = []
    block = {
        "height": 1,
        "previous_hash": GENESIS_HASH,
        "block_hash": "d" * 64,
        "timestamp": "2026-06-04T00:00:00+00:00",
    }

    first = client.post(
        "/node/blocks/receive?gossip=false",
        json={"block": block, "source_peer_id": "peer-a"},
    )
    assert first.status_code == 200
    assert first.json()["status"] == "pending_replay"

    monkeypatch.setattr(
        "app.api.routes.gossip_json",
        lambda *args, **kwargs: calls.append({"args": args, "kwargs": kwargs}) or {},
    )
    duplicate = client.post(
        "/node/blocks/receive",
        json={"block": block, "source_peer_id": "peer-b"},
    )

    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "known"
    assert duplicate.json()["reason"] == "block header already queued"
    assert calls == []


def test_tasks_next_endpoint_does_not_502_when_protocol_params_has_retarget_fields(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)
    with get_connection() as connection:
        connection.execute(
            "UPDATE protocol_params SET retarget_source_window = 20, retarget_source_details = '{\"epoch_block_count\":20,\"reason\":\"test\"}' WHERE active = 1"
        )
    public_key = generate_keypair()["public_key"]
    miner = register_miner("route-test-miner", public_key)
    response = client.get("/tasks/next", params={"miner_id": miner["miner_id"]})

    assert response.status_code == 200
    assert response.json()["task_id"].startswith("task_")


def test_mining_metrics_endpoint_handles_empty_chain(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)
    response = client.get("/mining/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["blocks_sampled"] == 0
    assert payload["blocks"] == []
    assert payload["top_miners"] == []


def test_miner_lookup_returns_reward_wallet_activity(tmp_path, monkeypatch) -> None:
    client = _build_test_client(tmp_path, monkeypatch)
    reward_wallet = create_wallet("api-miner-reward")
    miner = register_miner("lookup-miner", generate_keypair()["public_key"], reward_wallet["address"])
    with get_connection() as connection:
        protocol_params_id = connection.execute("SELECT id FROM protocol_params WHERE active = 1").fetchone()["id"]
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                protocol_params_id, created_at, submitted_at, compute_ms
            )
            VALUES ('task_lookup_1', ?, 1, 64, 'bbp_hex_v1', 'accepted', ?, '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:02+00:00', 1000)
            """,
            (miner["miner_id"], protocol_params_id),
        )
        connection.execute(
            """
            INSERT INTO blocks (
                height, previous_hash, miner_id, range_start, range_end, algorithm,
                result_hash, samples, timestamp, block_hash, reward, miner_reward_address,
                difficulty, task_id, protocol_params_id, protocol_version, validation_mode,
                total_task_ms, total_block_ms, validation_ms
            )
            VALUES (
                1, ?, ?, 1, 64, 'bbp_hex_v1',
                ?, '[]', '2026-06-01T00:00:03+00:00', ?, 2.51328, ?,
                4.0, 'task_lookup_1', ?, '1.0', 'external_commit_reveal',
                2000, 3000, 500
            )
            """,
            ("0" * 64, miner["miner_id"], "a" * 64, "b" * 64, reward_wallet["address"], protocol_params_id),
        )
        connection.execute(
            "UPDATE miners SET online_status = 'online', last_compute_ms = 500 WHERE miner_id = ?",
            (miner["miner_id"],),
        )

    metrics_response = client.get("/mining/metrics?limit=10")
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    assert metrics["summary"]["blocks_sampled"] == 1
    assert metrics["blocks"][0]["work_rate_hps"] == 64.0
    assert metrics["summary"]["avg_accepted_block_work_rate_hps"] == 64.0
    assert metrics["summary"]["network_compute_rate_hps"] == 128.0

    lookup_response = client.get(f"/miners/lookup/{reward_wallet['address']}")
    assert lookup_response.status_code == 200
    lookup = lookup_response.json()
    assert lookup["found"] is True
    assert lookup["summary"]["accepted_blocks"] == 1
    assert lookup["summary"]["avg_work_rate_hps"] == 64.0
    assert lookup["recent_blocks"][0]["result_hash"] == "a" * 64
