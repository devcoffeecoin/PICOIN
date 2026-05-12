import pytest

from app.db.database import get_connection, init_db
from app.services.treasury import (
    TreasuryError,
    claim_scientific_development_treasury,
    get_scientific_development_treasury,
    record_scientific_development_treasury_for_block,
)


def test_scientific_development_treasury_accumulates_locked_three_percent(tmp_path, monkeypatch) -> None:
    _init_treasury_db(tmp_path, monkeypatch, "treasury-locked.sqlite3")

    with get_connection() as connection:
        amount = record_scientific_development_treasury_for_block(connection, 1, 3.1416)

    treasury = get_scientific_development_treasury()

    assert amount == 0.094248
    assert treasury["total_accumulated"] == 0.094248
    assert treasury["locked_balance"] == 0.094248
    assert treasury["unlocked_balance"] == 0.0
    assert treasury["claimable"] == 0.0
    assert treasury["unlock_interval_days"] == 90
    assert treasury["history"][0]["status"] == "locked"

    with pytest.raises(TreasuryError):
        claim_scientific_development_treasury()


def test_scientific_development_treasury_unlocks_after_timelock_and_claims_once(tmp_path, monkeypatch) -> None:
    _init_treasury_db(tmp_path, monkeypatch, "treasury-unlocked.sqlite3")
    monkeypatch.setattr("app.services.treasury.SCIENTIFIC_DEVELOPMENT_UNLOCK_INTERVAL_DAYS", 0)

    with get_connection() as connection:
        record_scientific_development_treasury_for_block(connection, 1, 3.1416)

    unlocked = get_scientific_development_treasury()
    assert unlocked["locked_balance"] == 0.0
    assert unlocked["unlocked_balance"] == 0.094248

    claimed = claim_scientific_development_treasury()

    assert claimed["total_claimed"] == 0.094248
    assert claimed["locked_balance"] == 0.0
    assert claimed["unlocked_balance"] == 0.0
    assert claimed["claim"]["amount"] == 0.094248
    assert claimed["history"][0]["claimed_amount"] == 0.094248

    with pytest.raises(TreasuryError):
        claim_scientific_development_treasury()


def test_scientific_development_treasury_rejects_wrong_governance_or_destination(tmp_path, monkeypatch) -> None:
    _init_treasury_db(tmp_path, monkeypatch, "treasury-governance.sqlite3")
    monkeypatch.setattr("app.services.treasury.SCIENTIFIC_DEVELOPMENT_UNLOCK_INTERVAL_DAYS", 0)

    with get_connection() as connection:
        record_scientific_development_treasury_for_block(connection, 1, 3.1416)

    with pytest.raises(TreasuryError):
        claim_scientific_development_treasury(requested_by="attacker")
    with pytest.raises(TreasuryError):
        claim_scientific_development_treasury(claim_to="wrong-wallet")


def _init_treasury_db(tmp_path, monkeypatch, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)
