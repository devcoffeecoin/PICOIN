from app.core.signatures import generate_keypair
from app.db.database import init_db
from app.services.mining import create_next_task, register_miner


def test_pseudo_random_assignment_returns_non_sequential_ranges(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "assignment.sqlite3"
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)

    first_keys = generate_keypair()
    second_keys = generate_keypair()
    first_miner = register_miner("assignment-a", first_keys["public_key"])
    second_miner = register_miner("assignment-b", second_keys["public_key"])

    first_task = create_next_task(first_miner["miner_id"])
    second_task = create_next_task(second_miner["miner_id"])

    assert first_task["assignment_mode"] == "pseudo_random"
    assert first_task["assignment_seed"]
    assert second_task["assignment_seed"]

    overlaps = first_task["range_start"] <= second_task["range_end"] and first_task["range_end"] >= second_task["range_start"]
    assert overlaps is False
