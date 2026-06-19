from __future__ import annotations

import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib import request

from picoin_forge_l2.common.hashing import hash_json
from picoin_forge_l2.common.models import (
    AIInferenceCreateRequest,
    AIInferenceResult,
    ChallengeType,
)
from picoin_forge_l2.worker.ai_model import run_ai_inference
from picoin_forge_l2.worker.benchmark import run_benchmark
from picoin_forge_l2.worker.challenges import solve_challenge
from picoin_forge_l2.worker.heartbeat import build_heartbeat
from picoin_forge_l2.worker.loop import run_worker_once
from picoin_forge_l2.worker.registration import register_worker

from .ai_access_queue import AIAccessQueue, ai_inference_receipt_payload
from .challenge_engine import ChallengeEngine
from .epoch_engine import EpochEngine
from .worker_registry import WorkerRegistry


def run_local_ai_http_devnet(
    state_dir: str | Path,
    *,
    worker_count: int = 1,
    requester_wallet: str = "PILOCALHTTPREQUESTER0001",
    stake_snapshot_pi: float = 25.0,
    prompt: str = "Explain Picoin Forge L2 HTTP devnet in one short paragraph.",
    epoch_reward: float = 100.0,
) -> dict:
    """Run a local HTTP smoke demo against the real coordinator API."""

    resolved_state_dir = Path(state_dir)
    coordinator_state_dir = resolved_state_dir / "coordinator"
    workers_state_dir = resolved_state_dir / "workers"
    coordinator_state_dir.mkdir(parents=True, exist_ok=True)
    workers_state_dir.mkdir(parents=True, exist_ok=True)

    # Import lazily to avoid a top-level circular import with coordinator.main.
    from . import main as coordinator_main

    import uvicorn

    original_state_dir = coordinator_main.DEFAULT_COORDINATOR_STATE_DIR
    coordinator_main.DEFAULT_COORDINATOR_STATE_DIR = str(coordinator_state_dir)
    port = _free_local_port()
    base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(
            coordinator_main.api,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    with _temporary_env(
        {
            "PICOIN_FORGE_COORDINATOR_TOKEN": "",
            "PICOIN_FORGE_REQUIRE_WORKER_SIGNATURES": "",
            "PICOIN_FORGE_AI_ACCESS_MIN_STAKE_PI": "1",
        }
    ):
        thread.start()
        try:
            _wait_for_health(base_url)
            worker_reports = []
            first_registration = None
            for index in range(max(1, worker_count)):
                worker_state_dir = workers_state_dir / f"worker-{index + 1}"
                with _local_ai_env(index):
                    registration = register_worker(f"PIHTTPAIWORKER{index + 1:04d}", worker_state_dir)
                first_registration = first_registration or registration
                _post_json(base_url, "/workers/register", registration.model_dump(mode="json"))
                ai_challenge = _post_json(
                    base_url,
                    "/challenges",
                    {
                        "worker_id": registration.worker_id,
                        "challenge_type": ChallengeType.AI_MODEL.value,
                        "difficulty": 1,
                    },
                )
                worker_reports.append(
                    {
                        "worker_id": registration.worker_id,
                        "wallet": registration.wallet,
                        "ai_challenge_id": ai_challenge["challenge_id"],
                        "model_profile": registration.ai_model_profile.model_dump(mode="json")
                        if registration.ai_model_profile
                        else None,
                    }
                )

            if first_registration is None:
                raise RuntimeError("no HTTP demo worker was registered")

            created_request = _post_json(
                base_url,
                "/ai/requests",
                {
                    "requester_wallet": requester_wallet,
                    "stake_snapshot_pi": stake_snapshot_pi,
                    "prompt": prompt,
                    "required_capabilities": ["chat", "reasoning"],
                    "preferred_provider": "test-ai-model",
                    "min_parameter_count_b": 7.0,
                    "min_context_tokens": 4096,
                    "max_tokens": 128,
                },
            )
            first_worker_dir = workers_state_dir / "worker-1"
            with _local_ai_env(0):
                worker_once = run_worker_once(
                    state_dir=first_worker_dir,
                    coordinator_url=base_url,
                    request_challenge=False,
                    benchmark_scale=1,
                )

            request_id = created_request["request_id"]
            status = _get_json(base_url, f"/ai/requests/{request_id}/status")
            result = _get_json(base_url, f"/ai/requests/{request_id}/result")
            receipt = _get_json(base_url, f"/ai/requests/{request_id}/receipt")
            capabilities = _get_json(base_url, "/ai/capabilities")
            settlement = _post_json(base_url, f"/epochs/close?epoch_reward={epoch_reward}", {})

            report = {
                "schema": "picoin-forge-local-ai-http-devnet-v1",
                "state_dir": str(resolved_state_dir),
                "base_url": base_url,
                "workers": worker_reports,
                "created_request": created_request,
                "worker_once": worker_once,
                "status": status,
                "result": result,
                "receipt": receipt,
                "capabilities": capabilities,
                "settlement": settlement,
                "http_smoke_passed": (
                    status.get("status") == "verified"
                    and bool(receipt.get("valid"))
                    and bool(result.get("output_hash"))
                    and bool(settlement.get("result_hash"))
                ),
            }
            report_path = resolved_state_dir / "local_ai_http_devnet_report.json"
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            report["report_path"] = str(report_path)
            return report
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            coordinator_main.DEFAULT_COORDINATOR_STATE_DIR = original_state_dir


def run_local_ai_devnet(
    state_dir: str | Path,
    *,
    worker_count: int = 2,
    requester_wallet: str = "PILOCALAIREQUESTER0001",
    stake_snapshot_pi: float = 25.0,
    prompt: str = "Explain Picoin Forge L2 in one short paragraph.",
    epoch_reward: float = 100.0,
) -> dict:
    """Run a single-process Picoin Forge L2 AI demo.

    This intentionally does not touch Picoin L1. It creates local coordinator
    state, registers workers with a deterministic test AI backend, verifies AI
    model capacity, serves one stake-gated request, and closes a simulated epoch.
    """

    resolved_state_dir = Path(state_dir)
    resolved_state_dir.mkdir(parents=True, exist_ok=True)
    registry = WorkerRegistry(resolved_state_dir)
    challenge_engine = ChallengeEngine(resolved_state_dir, registry)
    queue = AIAccessQueue(resolved_state_dir, registry)

    worker_summaries = []
    challenge_summaries = []
    for index in range(max(1, worker_count)):
        with _local_ai_env(index):
            worker_state_dir = resolved_state_dir / "workers" / f"worker-{index + 1}"
            registration = register_worker(f"PILOCALAIWORKER{index + 1:04d}", worker_state_dir)
            registry.register(registration)
            benchmark = run_benchmark(registration.worker_id, scale=1)
            registry.update_benchmark(benchmark)
            registry.heartbeat(build_heartbeat(registration.worker_id))

            for challenge_type in (ChallengeType.CPU, ChallengeType.RAM, ChallengeType.IO, ChallengeType.AI_MODEL):
                challenge = challenge_engine.create_challenge(registration.worker_id, challenge_type, difficulty=1)
                result = solve_challenge(challenge)
                verified = challenge_engine.verify_result(result)
                challenge_summaries.append(
                    {
                        "worker_id": registration.worker_id,
                        "challenge_id": challenge.challenge_id,
                        "challenge_type": challenge.challenge_type.value,
                        "verified": verified,
                    }
                )

            state = registry.get(registration.worker_id)
            worker_summaries.append(
                {
                    "worker_id": registration.worker_id,
                    "wallet": registration.wallet,
                    "model_profile": registration.ai_model_profile.model_dump(mode="json")
                    if registration.ai_model_profile
                    else None,
                    "ai_model_score": state.ai_model_score,
                    "benchmark_score": benchmark.benchmark_score,
                }
            )

    created_request = queue.create(
        AIInferenceCreateRequest(
            requester_wallet=requester_wallet,
            stake_snapshot_pi=stake_snapshot_pi,
            prompt=prompt,
            required_capabilities=["chat", "reasoning"],
            preferred_provider="test-ai-model",
            min_parameter_count_b=7.0,
            min_context_tokens=4096,
            max_tokens=128,
        )
    )
    selected_worker_id = None
    claimed_request = None
    for worker in registry.all():
        claimed_request = queue.claim_next(worker.registration.worker_id)
        if claimed_request is not None:
            selected_worker_id = worker.registration.worker_id
            break
    if claimed_request is None or selected_worker_id is None:
        raise RuntimeError("local AI request was not claimable by any demo worker")

    with _test_backend_only_env():
        inference = run_ai_inference(
            claimed_request.prompt,
            max_tokens=claimed_request.max_tokens,
            profile=claimed_request.model_profile,
        )
    if not inference.accepted:
        raise RuntimeError(f"local AI inference failed: {inference.reason}")

    verified_request = queue.submit(
        AIInferenceResult(
            request_id=claimed_request.request_id,
            worker_id=selected_worker_id,
            output=inference.output,
        )
    )
    receipt_payload = ai_inference_receipt_payload(verified_request)
    computed_receipt_hash = hash_json(receipt_payload)
    receipt_valid = computed_receipt_hash == verified_request.receipt_hash

    settlement = EpochEngine(resolved_state_dir, registry).close_epoch(epoch_reward=epoch_reward)
    refreshed_workers = [
        {
            "worker_id": state.registration.worker_id,
            "wallet": state.registration.wallet,
            "ai_model_score": state.ai_model_score,
            "verified_compute_score": state.verified_compute_score,
        }
        for state in registry.all()
    ]

    report = {
        "schema": "picoin-forge-local-ai-devnet-v1",
        "state_dir": str(resolved_state_dir),
        "workers_created": len(worker_summaries),
        "workers": worker_summaries,
        "refreshed_workers": refreshed_workers,
        "challenges": challenge_summaries,
        "challenge_pass_count": sum(1 for challenge in challenge_summaries if challenge["verified"]),
        "ai_request": {
            "request_id": verified_request.request_id,
            "status": verified_request.status.value,
            "requester_wallet": verified_request.requester_wallet,
            "stake_snapshot_pi": verified_request.stake_snapshot_pi,
            "required_stake_pi": verified_request.required_stake_pi,
            "assigned_worker_id": verified_request.assigned_worker_id,
            "assignment_attempts": verified_request.assignment_attempts,
            "prompt_hash": verified_request.prompt_hash,
            "output_hash": verified_request.output_hash,
            "receipt_hash": verified_request.receipt_hash,
            "receipt_valid": receipt_valid,
            "model_profile": verified_request.model_profile.model_dump(mode="json")
            if verified_request.model_profile
            else None,
            "no_l1_transaction_created": verified_request.no_l1_transaction_created,
            "no_per_task_payment": verified_request.no_per_task_payment,
        },
        "ai_output_preview": inference.output[:240],
        "settlement": settlement.model_dump(mode="json"),
        "receipt_payload": receipt_payload,
    }
    report_path = resolved_state_dir / "local_ai_devnet_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


@contextmanager
def _local_ai_env(index: int) -> Iterator[None]:
    with _temporary_env(
        {
            "PICOIN_FORGE_TEST_AI_MODEL_BACKEND": "1",
            "PICOIN_FORGE_AI_MODEL_PROVIDER": "test-ai-model",
            "PICOIN_FORGE_AI_MODEL_NAME": f"picoin-forge-local-llm-{index + 1}",
            "PICOIN_FORGE_AI_MODEL_PARAMETERS_B": str(7 + index),
            "PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS": str(8192 + (index * 2048)),
            "PICOIN_FORGE_AI_MODEL_QUANTIZATION": "local-demo",
            "PICOIN_FORGE_AI_MODEL_CAPABILITIES": "llm,chat,reasoning",
            "PICOIN_FORGE_AI_MODEL_ENDPOINT": "local://picoin-forge-test-ai-model",
        }
    ):
        yield


@contextmanager
def _test_backend_only_env() -> Iterator[None]:
    with _temporary_env({"PICOIN_FORGE_TEST_AI_MODEL_BACKEND": "1"}):
        yield


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, *, timeout_seconds: float = 15.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            health = _get_json(base_url, "/health")
            if health.get("status") == "ok":
                return
        except OSError as exc:
            last_error = exc
        time.sleep(0.1)
    raise TimeoutError(f"coordinator did not become healthy: {last_error}")


def _get_json(base_url: str, path: str) -> dict:
    with request.urlopen(base_url.rstrip("/") + path, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(base_url: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


@contextmanager
def _temporary_env(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value
