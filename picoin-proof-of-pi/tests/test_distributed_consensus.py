import json

import pytest

from app.core.crypto import hash_block
from app.core.settings import GENESIS_HASH, PROTOCOL_VERSION, REQUIRED_VALIDATOR_APPROVALS, VALIDATION_MODE
from app.core.signatures import generate_keypair, sign_payload
from app.db.database import init_db
from app.services.consensus import (
    ConsensusError,
    consensus_status,
    consensus_vote_payload,
    finalize_proposal,
    list_fork_choice_groups,
    propose_block,
    select_fork_choice,
    vote_on_proposal,
)
from app.services.mining import get_balance, get_block, register_validator, verify_chain
from app.services.science import get_science_reserve
from app.services.treasury import get_scientific_development_treasury


def _init_consensus_db(tmp_path, monkeypatch, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setattr("app.db.database.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.core.settings.DATABASE_PATH", db_path)
    init_db(db_path)


def _block(height: int = 1, previous_hash: str = GENESIS_HASH) -> dict:
    block = {
        "height": height,
        "previous_hash": previous_hash,
        "miner_id": "distributed-miner",
        "range_start": 1,
        "range_end": 64,
        "algorithm": "bbp_hex_v1",
        "result_hash": "a" * 64,
        "merkle_root": "b" * 64,
        "samples": [{"position": 1, "digit": "2"}],
        "timestamp": "2026-05-12T00:00:00+00:00",
        "reward": 2.104872,
        "difficulty": 4.0,
        "protocol_params_id": 1,
        "protocol_version": PROTOCOL_VERSION,
        "validation_mode": VALIDATION_MODE,
        "task_id": "distributed-task-1",
    }
    payload = {
        "algorithm": block["algorithm"],
        "height": block["height"],
        "miner_id": block["miner_id"],
        "previous_hash": block["previous_hash"],
        "range_end": block["range_end"],
        "range_start": block["range_start"],
        "result_hash": block["result_hash"],
        "reward": block["reward"],
        "samples": block["samples"],
        "timestamp": block["timestamp"],
        "difficulty": block["difficulty"],
        "protocol_params_id": block["protocol_params_id"],
        "merkle_root": block["merkle_root"],
        "protocol_version": block["protocol_version"],
        "validation_mode": block["validation_mode"],
    }
    block["block_hash"] = hash_block(payload)
    return block


def _rehash(block: dict) -> dict:
    payload = {
        "algorithm": block["algorithm"],
        "height": block["height"],
        "miner_id": block["miner_id"],
        "previous_hash": block["previous_hash"],
        "range_end": block["range_end"],
        "range_start": block["range_start"],
        "result_hash": block["result_hash"],
        "reward": block["reward"],
        "samples": block["samples"],
        "timestamp": block["timestamp"],
        "difficulty": block["difficulty"],
        "protocol_params_id": block["protocol_params_id"],
        "merkle_root": block["merkle_root"],
        "protocol_version": block["protocol_version"],
        "validation_mode": block["validation_mode"],
    }
    block["block_hash"] = hash_block(payload)
    return block


def _register_validators(count: int = REQUIRED_VALIDATOR_APPROVALS) -> list[dict]:
    identities = []
    for index in range(count):
        keys = generate_keypair()
        validator = register_validator(f"validator-{index}", keys["public_key"])
        identities.append({**validator, **keys})
    return identities


def _vote(proposal: dict, identity: dict, approved: bool = True, reason: str = "accepted") -> dict:
    signed_at = "2026-05-12T00:00:01+00:00"
    payload = consensus_vote_payload(
        proposal_id=proposal["proposal_id"],
        block_hash=proposal["block_hash"],
        height=proposal["height"],
        validator_id=identity["validator_id"],
        approved=approved,
        reason=reason,
        signed_at=signed_at,
    )
    signature = sign_payload(identity["private_key"], payload)
    return vote_on_proposal(proposal["proposal_id"], identity["validator_id"], approved, reason, signature, signed_at)


def _insert_competing_proposal(block: dict, previous_hash: str, approvals: int = 0, created_at: str = "2026-05-12T00:00:02+00:00") -> dict:
    from app.core.crypto import sha256_text
    from app.db.database import get_connection

    candidate = {**block, "previous_hash": previous_hash}
    candidate["block_hash"] = sha256_text(f"external-fork:{previous_hash}:{approvals}:{candidate['height']}:{created_at}")
    proposal_id = sha256_text(f"proposal:{candidate['block_hash']}")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO consensus_block_proposals (
                proposal_id, block_hash, height, previous_hash, proposer_node_id,
                status, payload, approvals, rejections, rejection_reason,
                finalized_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'external-node', 'pending', ?, ?, 0, NULL, NULL, ?, ?)
            """,
            (
                proposal_id,
                candidate["block_hash"],
                candidate["height"],
                candidate["previous_hash"],
                json.dumps(candidate, sort_keys=True),
                approvals,
                created_at,
                created_at,
            ),
        )
    return {
        "proposal_id": proposal_id,
        "block_hash": candidate["block_hash"],
        "height": candidate["height"],
        "previous_hash": candidate["previous_hash"],
        "approvals": approvals,
    }


def test_block_proposal_reaches_quorum_and_imports_canonical_block(tmp_path, monkeypatch) -> None:
    _init_consensus_db(tmp_path, monkeypatch, "consensus-import.sqlite3")
    identities = _register_validators()
    proposal = propose_block(_block(), "miner-node-1")

    for identity in identities:
        proposal = _vote(proposal, identity)

    imported = get_block(1)
    chain = verify_chain()
    reserve = get_science_reserve()
    treasury = get_scientific_development_treasury()

    assert proposal["status"] == "imported"
    assert imported["block_hash"] == proposal["block_hash"]
    assert get_balance("distributed-miner")["balance"] == 2.104872
    assert get_balance(identities[0]["validator_id"])["balance"] == 0.10472
    assert reserve["total_reserved"] == 0.62832
    assert treasury["locked_balance"] == 0.094248
    assert chain["valid"] is True


def test_finalize_requires_validator_quorum(tmp_path, monkeypatch) -> None:
    _init_consensus_db(tmp_path, monkeypatch, "consensus-quorum.sqlite3")
    identity = _register_validators(1)[0]
    proposal = propose_block(_block(), "miner-node-1")
    _vote(proposal, identity)

    with pytest.raises(ConsensusError, match="quorum not reached"):
        finalize_proposal(proposal["proposal_id"])


def test_invalid_block_proposal_is_rejected(tmp_path, monkeypatch) -> None:
    _init_consensus_db(tmp_path, monkeypatch, "consensus-invalid.sqlite3")
    block = _block()
    block["reward"] = 99

    with pytest.raises(ConsensusError, match="block_hash"):
        propose_block(block, "miner-node-1")


def test_rejection_quorum_rejects_proposal(tmp_path, monkeypatch) -> None:
    _init_consensus_db(tmp_path, monkeypatch, "consensus-reject.sqlite3")
    identities = _register_validators()
    proposal = propose_block(_block(), "miner-node-1")

    for identity in identities:
        proposal = _vote(proposal, identity, approved=False, reason="bad samples")

    assert proposal["status"] == "rejected"
    assert get_block(1) is None


def test_fork_choice_prefers_more_approvals_and_blocks_double_vote(tmp_path, monkeypatch) -> None:
    _init_consensus_db(tmp_path, monkeypatch, "consensus-fork-choice.sqlite3")
    identity = _register_validators(1)[0]
    first = propose_block(_block(), "miner-node-1")
    fork_block = _block()
    fork_block["range_start"] = 65
    fork_block["range_end"] = 128
    fork_block["result_hash"] = "c" * 64
    fork_block["task_id"] = "distributed-task-fork"
    second = propose_block(_rehash(fork_block), "miner-node-2")

    first = _vote(first, identity)
    winner = select_fork_choice(1)

    assert winner["proposal_id"] == first["proposal_id"]
    assert winner["proposal_id"] != second["proposal_id"]
    with pytest.raises(ConsensusError, match="competing fork"):
        _vote(second, identity)


def test_fork_choice_uses_validator_reputation_and_stake_weight(tmp_path, monkeypatch) -> None:
    _init_consensus_db(tmp_path, monkeypatch, "consensus-weighted-fork.sqlite3")
    low_weight, high_weight = _register_validators(2)
    from app.db.database import get_connection

    with get_connection() as connection:
        connection.execute(
            "UPDATE validators SET trust_score = 0.5, stake_locked = 31.416 WHERE validator_id = ?",
            (low_weight["validator_id"],),
        )
        connection.execute(
            "UPDATE validators SET trust_score = 1.0, stake_locked = 314.16 WHERE validator_id = ?",
            (high_weight["validator_id"],),
        )

    first = propose_block(_block(), "miner-node-1")
    fork_block = _block()
    fork_block["range_start"] = 65
    fork_block["range_end"] = 128
    fork_block["result_hash"] = "d" * 64
    fork_block["task_id"] = "distributed-task-weighted-fork"
    second = propose_block(_rehash(fork_block), "miner-node-2")

    _vote(first, low_weight)
    _vote(second, high_weight)
    winner = select_fork_choice(1)

    assert winner["proposal_id"] == second["proposal_id"]
    assert winner["approval_weight"] > 10


def test_fork_choice_competes_only_with_same_parent(tmp_path, monkeypatch) -> None:
    _init_consensus_db(tmp_path, monkeypatch, "consensus-parent-scoped-fork.sqlite3")
    first = propose_block(_block(), "miner-node-1")
    fork_block = _block()
    fork_block["range_start"] = 65
    fork_block["range_end"] = 128
    fork_block["result_hash"] = "e" * 64
    fork_block["task_id"] = "distributed-task-parent-scope"
    second = propose_block(_rehash(fork_block), "miner-node-2")
    unrelated = _insert_competing_proposal(_block(), previous_hash="f" * 64, approvals=99)

    winner = select_fork_choice(1, GENESIS_HASH)
    groups = list_fork_choice_groups()

    assert winner["previous_hash"] == GENESIS_HASH
    assert winner["proposal_id"] in {first["proposal_id"], second["proposal_id"]}
    assert winner["proposal_id"] != unrelated["proposal_id"]
    assert len(groups) == 1
    assert groups[0]["previous_hash"] == GENESIS_HASH
    assert {candidate["proposal_id"] for candidate in groups[0]["candidates"]} == {
        first["proposal_id"],
        second["proposal_id"],
    }


def test_parent_scoped_fork_choice_does_not_block_finalization(tmp_path, monkeypatch) -> None:
    _init_consensus_db(tmp_path, monkeypatch, "consensus-parent-finalization.sqlite3")
    identities = _register_validators()
    proposal = propose_block(_block(), "miner-node-1")
    _insert_competing_proposal(_block(), previous_hash="f" * 64, approvals=99)

    for identity in identities:
        proposal = _vote(proposal, identity)

    assert proposal["status"] == "imported"
    assert get_block(1)["block_hash"] == proposal["block_hash"]


def test_fork_choice_tie_breaks_by_oldest_then_lowest_hash(tmp_path, monkeypatch) -> None:
    _init_consensus_db(tmp_path, monkeypatch, "consensus-deterministic-tie.sqlite3")
    first = _insert_competing_proposal(
        _block(),
        previous_hash=GENESIS_HASH,
        created_at="2026-05-12T00:00:03+00:00",
    )
    second = _insert_competing_proposal(
        _block(),
        previous_hash=GENESIS_HASH,
        created_at="2026-05-12T00:00:02+00:00",
    )

    winner = select_fork_choice(1, GENESIS_HASH)

    assert winner["proposal_id"] == second["proposal_id"]
    assert winner["proposal_id"] != first["proposal_id"]


def test_consensus_status_reports_proposals_and_finalizations(tmp_path, monkeypatch) -> None:
    _init_consensus_db(tmp_path, monkeypatch, "consensus-status.sqlite3")
    propose_block(_block(), "miner-node-1")

    status = consensus_status()

    assert status["required_validator_approvals"] == 3
    assert "same height and previous_hash compete" in status["fork_choice_rule"]
    assert status["proposals"]["pending"] == 1
    assert status["fork_group_count"] == 0
    assert status["latest_block_height"] == 0
