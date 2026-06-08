from app.core.signatures import generate_keypair, sign_payload
from app.db.database import init_db
from app.db.database import get_connection
from app.models.schemas import NodeEventResponse
from app.services.mining import (
    get_protocol,
    get_health_status,
    get_node_status,
    get_recent_events,
    record_validator_heartbeat,
    register_miner,
    register_validator,
    request_faucet,
)
from app.services.network import get_sync_status


def test_health_reports_empty_node_as_degraded_but_connected(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "health.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    health = get_health_status()

    assert health["status"] == "degraded"
    assert health["database"]["connected"] is True
    assert health["readiness"]["read_ready"] is True
    assert health["readiness"]["task_assign_ready"] is False
    assert health["readiness"]["block_finalize_ready"] is False
    assert health["can_assign_tasks"] is False
    assert health["mining_ready"] is False
    assert "eligible validators 0 below required quorum" in health["readiness"]["reasons"]["task_assign_ready"][0]
    assert "not enough eligible validators for quorum" in health["issues"]


def test_phase9_readiness_is_exposed_on_protocol_and_sync_status(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "phase9-readiness.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    protocol = get_protocol()
    sync = get_sync_status()

    assert protocol["node_role"] == "write_candidate"
    assert protocol["node_capabilities"]["task_assign"] is True
    assert sync["readiness"]["read_ready"] is True
    assert sync["readiness"]["task_assign_ready"] is False
    assert sync["readiness"]["block_finalize_ready"] is False
    assert "eligible validators 0 below required quorum" in sync["readiness"]["reasons"]["block_finalize_ready"][0]


def test_node_status_and_events_report_operational_testnet(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "node-status.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    miner_key = generate_keypair()
    first_validator_key = generate_keypair()
    second_validator_key = generate_keypair()
    third_validator_key = generate_keypair()
    miner = register_miner("node-miner", miner_key["public_key"])
    first_validator = register_validator("node-validator-one", first_validator_key["public_key"])
    second_validator = register_validator("node-validator-two", second_validator_key["public_key"])
    third_validator = register_validator("node-validator-three", third_validator_key["public_key"])
    _heartbeat_validator(first_validator, first_validator_key, "node-validator-one")
    _heartbeat_validator(second_validator, second_validator_key, "node-validator-two")
    _heartbeat_validator(third_validator, third_validator_key, "node-validator-three")
    request_faucet(miner["miner_id"], "miner", 1.0)

    health = get_health_status()
    status = get_node_status()
    events = get_recent_events()

    assert health["status"] == "ok"
    assert health["readiness"]["read_ready"] is True
    assert health["readiness"]["task_assign_ready"] is True
    assert health["readiness"]["block_finalize_ready"] is True
    assert health["can_assign_tasks"] is True
    assert health["mining_ready"] is True
    assert status["counts"]["miners"] == 1
    assert status["counts"]["eligible_validators"] == 3
    assert status["mining_ready"] is True
    assert any(event["type"] == "faucet_credit" for event in events)


def test_node_events_normalize_science_event_ids(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "node-science-events.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO science_events (event_type, address, job_id, payload, created_at)
            VALUES ('ScienceReserveAccrued', NULL, NULL, ?, '2026-05-10T00:00:00+00:00')
            """,
            ('{"block_height": 1, "amount": 0.219912, "epoch": "2026-05"}',),
        )

    events = get_recent_events()
    science_event = next(event for event in events if event["type"] == "ScienceReserveAccrued")

    assert science_event["id"] == "science:1"
    NodeEventResponse.model_validate(science_event)


def _heartbeat_validator(validator: dict, keys: dict, node_id: str) -> None:
    payload = {
        "validator_id": validator["validator_id"],
        "name": validator["name"],
        "node_id": node_id,
        "public_key": keys["public_key"],
        "address": f"http://{node_id}:8000",
        "local_height": 0,
        "effective_height": 0,
        "latest_block_hash": "0" * 64,
        "pending_replay_blocks": 0,
        "sync_lag": 0,
        "version": "0.18",
    }
    payload["signature"] = sign_payload(keys["private_key"], payload)
    record_validator_heartbeat(payload)
