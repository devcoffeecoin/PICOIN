from __future__ import annotations

from picoin_forge_l2.coordinator.local_devnet import run_local_ai_devnet, run_local_ai_http_devnet


def test_local_ai_devnet_runs_end_to_end(tmp_path):
    result = run_local_ai_devnet(
        tmp_path,
        worker_count=2,
        requester_wallet="PITESTLOCALAI",
        stake_snapshot_pi=25.0,
        prompt="Explain the Picoin Forge L2 local devnet.",
        epoch_reward=50.0,
    )

    assert result["schema"] == "picoin-forge-local-ai-devnet-v1"
    assert result["workers_created"] == 2
    assert result["challenge_pass_count"] == 8
    assert result["ai_request"]["status"] == "verified"
    assert result["ai_request"]["receipt_valid"] is True
    assert result["ai_request"]["no_l1_transaction_created"] is True
    assert result["ai_request"]["no_per_task_payment"] is True
    assert result["ai_request"]["assigned_worker_id"]
    assert result["ai_request"]["output_hash"]
    assert result["ai_request"]["receipt_hash"]
    assert result["settlement"]["epoch_id"] == 1
    assert round(sum(worker["reward_pi"] for worker in result["settlement"]["workers"]), 8) == 50.0
    assert (tmp_path / "local_ai_devnet_report.json").exists()


def test_local_ai_http_devnet_exercises_public_api(tmp_path):
    result = run_local_ai_http_devnet(
        tmp_path,
        worker_count=1,
        requester_wallet="PITESTLOCALHTTPAI",
        stake_snapshot_pi=25.0,
        prompt="Explain the Picoin Forge L2 HTTP devnet.",
        epoch_reward=25.0,
    )

    assert result["schema"] == "picoin-forge-local-ai-http-devnet-v1"
    assert result["http_smoke_passed"] is True
    assert result["status"]["status"] == "verified"
    assert result["status"]["result_ready"] is True
    assert result["status"]["receipt_ready"] is True
    assert result["receipt"]["valid"] is True
    assert result["result"]["no_per_task_payment"] is True
    assert result["capabilities"]["ready_worker_count"] == 1
    assert result["settlement"]["epoch_id"] == 1
    assert round(sum(worker["reward_pi"] for worker in result["settlement"]["workers"]), 8) == 25.0
    assert (tmp_path / "local_ai_http_devnet_report.json").exists()
