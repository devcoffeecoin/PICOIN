from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routes import router
from app.core.signatures import generate_keypair
from app.db.database import DATABASE_PATH, get_connection, init_db
from app.services.mining import register_miner


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
