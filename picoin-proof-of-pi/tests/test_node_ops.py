from app.core.signatures import generate_keypair
from app.db.database import init_db
from app.db.database import get_connection
from app.models.schemas import NodeEventResponse
from app.services.mining import (
    get_health_status,
    get_node_status,
    get_recent_events,
    register_miner,
    register_validator,
    request_faucet,
)


def test_health_reports_empty_node_as_degraded_but_connected(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "health.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    health = get_health_status()

    assert health["status"] == "degraded"
    assert health["database"]["connected"] is True
    assert health["can_assign_tasks"] is True
    assert health["mining_ready"] is False
    assert "not enough eligible validators for quorum" in health["issues"]


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
    register_validator("node-validator-one", first_validator_key["public_key"])
    register_validator("node-validator-two", second_validator_key["public_key"])
    register_validator("node-validator-three", third_validator_key["public_key"])
    request_faucet(miner["miner_id"], "miner", 1.0)

    health = get_health_status()
    status = get_node_status()
    events = get_recent_events()

    assert health["status"] == "ok"
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
            ('{"block_height": 1, "amount": 0.62832, "epoch": "2026-05"}',),
        )

    events = get_recent_events()
    science_event = next(event for event in events if event["type"] == "ScienceReserveAccrued")

    assert science_event["id"] == "science:1"
    NodeEventResponse.model_validate(science_event)
