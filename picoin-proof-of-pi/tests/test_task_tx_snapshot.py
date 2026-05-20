from app.core.settings import CHAIN_ID, NETWORK_ID
from app.core.signatures import build_commit_signature_payload, generate_keypair, sign_payload
from app.db.database import get_connection, init_db
from app.services.mining import commit_task, create_next_task, get_balance, register_miner, request_faucet
from app.services.transactions import (
    freeze_transactions_for_task,
    release_selected_transactions,
    select_transactions_for_task,
)
from app.services.wallet import create_wallet, sign_transaction
from app.services.network import submit_transaction


def _setup_db(tmp_path, monkeypatch, name: str) -> None:
    db_path = tmp_path / f"{name}.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)


def _funded_wallet(amount: float = 2.0) -> dict:
    wallet = create_wallet("snapshot-wallet")
    request_faucet(wallet["address"], "wallet", amount)
    return wallet


def _submit_transfer(wallet: dict, recipient: str, amount: float, nonce: int, fee: float = 0.001) -> dict:
    tx = sign_transaction(
        private_key=wallet["private_key"],
        public_key=wallet["public_key"],
        tx_type="transfer",
        sender=wallet["address"],
        recipient=recipient,
        amount=amount,
        nonce=nonce,
        fee=fee,
    )
    return submit_transaction(tx)


def _insert_dummy_task(task_id: str) -> None:
    keypair = generate_keypair()
    miner = register_miner(f"miner-{task_id}", keypair["public_key"])
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, miner_id, range_start, range_end, algorithm, status,
                assignment_seed, assignment_mode, assignment_ms, protocol_params_id,
                created_at, expires_at
            )
            VALUES (?, ?, 1, 16, 'bbp_hex_v1', 'assigned', 'seed', 'test', 0, 1,
                    '2026-05-20T00:00:00+00:00', '2026-05-20T01:00:00+00:00')
            """,
            (task_id, miner["miner_id"]),
        )


def test_task_snapshot_freezes_current_mempool_only(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch, "task-snapshot-freeze")
    source = _funded_wallet(2.0)
    recipient = create_wallet("recipient")
    first = _submit_transfer(source, recipient["address"], 0.25, 1)
    _insert_dummy_task("task_freeze")

    with get_connection() as connection:
        snapshot = freeze_transactions_for_task(connection, task_id="task_freeze", block_height=1)

    second = _submit_transfer(source, recipient["address"], 0.25, 2)
    assert snapshot["tx_hashes"] == [first["tx_hash"]]

    with get_connection() as connection:
        first_row = connection.execute("SELECT status, selected_task_id FROM mempool_transactions WHERE tx_hash = ?", (first["tx_hash"],)).fetchone()
        second_row = connection.execute("SELECT status, selected_task_id FROM mempool_transactions WHERE tx_hash = ?", (second["tx_hash"],)).fetchone()

    assert first_row["status"] == "selected"
    assert first_row["selected_task_id"] == "task_freeze"
    assert second_row["status"] == "pending"
    assert second_row["selected_task_id"] is None


def test_double_spend_selects_only_affordable_transactions(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch, "task-snapshot-double-spend")
    source = _funded_wallet(1.0)
    recipient = create_wallet("recipient")
    first = _submit_transfer(source, recipient["address"], 0.75, 1, fee=0.001)
    second = _submit_transfer(source, recipient["address"], 0.75, 2, fee=0.001)

    with get_connection() as connection:
        selected = select_transactions_for_task(connection, 10, 0)

    assert [tx["tx_hash"] for tx in selected] == [first["tx_hash"]]
    assert get_balance(source["address"])["balance"] == 1.0
    assert second["status"] == "pending"


def test_release_selected_transactions_returns_valid_tx_to_pending(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch, "task-snapshot-release")
    source = _funded_wallet(1.0)
    recipient = create_wallet("recipient")
    tx = _submit_transfer(source, recipient["address"], 0.25, 1)
    _insert_dummy_task("task_release")

    with get_connection() as connection:
        freeze_transactions_for_task(connection, task_id="task_release", block_height=1)
        result = release_selected_transactions(connection, "task_release", "task expired")
        row = connection.execute("SELECT status, selected_task_id, released_at FROM mempool_transactions WHERE tx_hash = ?", (tx["tx_hash"],)).fetchone()

    assert result["released"] == 1
    assert row["status"] == "pending"
    assert row["selected_task_id"] is None
    assert row["released_at"] is not None


def test_commit_rejects_tx_commitment_mismatch(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch, "task-snapshot-commit-mismatch")
    source = _funded_wallet(1.0)
    recipient = create_wallet("recipient")
    _submit_transfer(source, recipient["address"], 0.25, 1)
    miner_keypair = generate_keypair()
    miner = register_miner("snapshot-miner", miner_keypair["public_key"])
    task = create_next_task(miner["miner_id"])

    signed_at = "2026-05-20T00:00:00+00:00"
    wrong_tx_root = "f" * 64
    payload = build_commit_signature_payload(
        task_id=task["task_id"],
        miner_id=miner["miner_id"],
        range_start=task["range_start"],
        range_end=task["range_end"],
        algorithm=task["algorithm"],
        result_hash="a" * 64,
        merkle_root="b" * 64,
        signed_at=signed_at,
        tx_merkle_root=wrong_tx_root,
        mempool_snapshot_id=task["mempool_snapshot_id"],
        selected_tx_hashes_hash=task["selected_tx_hashes_hash"],
        tx_count=int(task["tx_count"]),
        tx_fee_total_units=int(task["tx_fee_total_units"]),
        chain_id=CHAIN_ID,
        network_id=NETWORK_ID,
    )
    signature = sign_payload(miner_keypair["private_key"], payload)

    result = commit_task(
        task_id=task["task_id"],
        miner_id=miner["miner_id"],
        result_hash="a" * 64,
        merkle_root="b" * 64,
        signature=signature,
        signed_at=signed_at,
        tx_merkle_root=wrong_tx_root,
        mempool_snapshot_id=task["mempool_snapshot_id"],
        selected_tx_hashes_hash=task["selected_tx_hashes_hash"],
        tx_count=int(task["tx_count"]),
        tx_fee_total_units=int(task["tx_fee_total_units"]),
    )

    assert result["accepted"] is False
    assert result["message"] == "invalid_tx_commitment"
