from __future__ import annotations

from picoin_forge_l2.coordinator.federation import run_federated_demo, verify_federated_manifest


def test_federated_demo_builds_independent_coordinator_manifests(tmp_path):
    result = run_federated_demo(tmp_path, coordinator_count=2, workers_per_coordinator=2, epoch_reward=50.0)

    assert result["schema_version"] == "picoin-forge-l2-federation-demo-v1"
    assert result["federation_result_hash"]
    assert result["no_l1_transaction_created"] is True
    assert len(result["coordinators"]) == 2
    assert {row["coordinator_id"] for row in result["coordinators"]} == {"coordinator-1", "coordinator-2"}

    for coordinator in result["coordinators"]:
        assert coordinator["workers_created"] == 2
        assert coordinator["challenges_passed"] == 6
        assert coordinator["settlement"]["epoch_reward"] == 50.0
        assert coordinator["l1_preview"]["payload_hash"]
        assert coordinator["l1_preview"]["no_l1_transaction_created"] is True


def test_federated_manifest_verifier_accepts_valid_manifest(tmp_path):
    result = run_federated_demo(tmp_path, coordinator_count=2, workers_per_coordinator=1, epoch_reward=25.0)
    verification = verify_federated_manifest(result)

    assert verification["valid"] is True
    assert verification["root_hash_matches"] is True
    assert verification["errors"] == []


def test_federated_manifest_verifier_rejects_tampered_manifest(tmp_path):
    result = run_federated_demo(tmp_path, coordinator_count=1, workers_per_coordinator=1, epoch_reward=25.0)
    result["coordinators"][0]["l1_preview"]["payload_hash"] = "tampered"
    verification = verify_federated_manifest(result)

    assert verification["valid"] is False
    assert any("payload hash mismatch" in error for error in verification["errors"])
