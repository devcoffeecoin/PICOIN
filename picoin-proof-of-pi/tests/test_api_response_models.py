from app.core.signatures import generate_keypair
from app.db.database import get_connection, init_db
from app.models.schemas import NodeSyncStatusResponse, ProtocolParamsResponse, ProtocolResponse, TaskResponse
from app.services.mining import create_next_task, get_protocol, get_protocol_history, register_miner
from app.services.network import get_sync_status


def _setup_db(tmp_path, monkeypatch, name: str) -> None:
    db_path = tmp_path / f"{name}.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)


def test_node_sync_status_response_validates_with_retarget_source_window_int(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch, "sync-status-retarget-window")
    with get_connection() as connection:
        connection.execute(
            "UPDATE protocol_params SET retarget_source_window = 20, retarget_reason = 'test' WHERE active = 1"
        )

    response = NodeSyncStatusResponse.model_validate(get_sync_status())
    assert response.effective_latest_block_height == 0


def test_protocol_params_response_accepts_integer_retarget_source_window(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch, "protocol-retarget-window")
    with get_connection() as connection:
        connection.execute(
            "UPDATE protocol_params SET retarget_source_window = 20, retarget_source_details = '{\"epoch_block_count\":20}' WHERE active = 1"
        )

    protocol = ProtocolResponse.model_validate(get_protocol())
    params = ProtocolParamsResponse.model_validate(get_protocol_history()[0])

    assert protocol.retarget_source_window == 20
    assert params.retarget_source_window == 20
    assert params.retarget_source_details == '{"epoch_block_count":20}'


def test_tasks_next_does_not_500_when_protocol_params_has_retarget_fields(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch, "task-next-retarget-window")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE protocol_params
            SET retarget_source_window = 20,
                retarget_source_details = '{"epoch_block_count":20,"reason":"test"}'
            WHERE active = 1
            """
        )
    keypair = generate_keypair()
    miner = register_miner("response-model-miner", keypair["public_key"])

    task = TaskResponse.model_validate(create_next_task(miner["miner_id"]))

    assert task.task_id.startswith("task_")
    assert task.tx_count == 0
