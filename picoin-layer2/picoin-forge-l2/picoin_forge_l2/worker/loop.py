from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from picoin_forge_l2.common.models import AIInferenceResult

from .ai_model import run_ai_inference
from .benchmark import run_benchmark
from .challenges import solve_challenge
from .client import CoordinatorClient
from .config import load_worker_config, worker_state_dir
from .heartbeat import build_heartbeat
from .registration import load_private_key, load_registration


def run_worker_once(
    *,
    state_dir: str | Path,
    coordinator_url: str | None = None,
    benchmark_scale: int | None = None,
    request_challenge: bool | None = None,
) -> dict:
    state_path = worker_state_dir(state_dir)
    config = load_worker_config(state_path, required=False)
    registration = load_registration(state_path)
    resolved_url = (coordinator_url or (config.coordinator_url if config else "http://127.0.0.1:9380")).rstrip("/")
    resolved_scale = benchmark_scale if benchmark_scale is not None else (config.benchmark_scale if config else 1)
    should_request_challenge = (
        request_challenge if request_challenge is not None else (config.request_challenges if config else True)
    )
    client = CoordinatorClient(
        resolved_url,
        private_key=load_private_key(state_path),
        worker_id=registration.worker_id,
    )
    client.register(registration)

    benchmark = run_benchmark(registration.worker_id, scale=resolved_scale)
    (state_path / "benchmark.json").write_text(benchmark.model_dump_json(indent=2), encoding="utf-8")
    client.submit_benchmark(benchmark)

    heartbeat = build_heartbeat(registration.worker_id)
    (state_path / "heartbeat.json").write_text(heartbeat.model_dump_json(indent=2), encoding="utf-8")
    client.heartbeat(heartbeat)

    solved = []
    challenges = client.open_challenges(registration.worker_id)
    if should_request_challenge and not challenges:
        challenges = [client.request_challenge(registration.worker_id)]
    for challenge in challenges:
        result = solve_challenge(challenge)
        client.submit_challenge_result(challenge.challenge_id, result)
        solved.append(result.model_dump(mode="json"))

    ai_requests_handled = []
    ai_request = client.claim_ai_request(registration.worker_id) if hasattr(client, "claim_ai_request") else None
    if ai_request is not None and hasattr(client, "submit_ai_request_result"):
        inference = run_ai_inference(
            ai_request.prompt,
            max_tokens=ai_request.max_tokens,
            profile=registration.ai_model_profile,
        )
        if inference.accepted:
            ai_result = AIInferenceResult(
                request_id=ai_request.request_id,
                worker_id=registration.worker_id,
                output=inference.output,
            )
            client.submit_ai_request_result(ai_request.request_id, ai_result)
            ai_requests_handled.append(
                {
                    "request_id": ai_request.request_id,
                    "backend": inference.backend,
                    "output_hash": inference.output_hash,
                }
            )
        else:
            ai_requests_handled.append(
                {
                    "request_id": ai_request.request_id,
                    "accepted": False,
                    "backend": inference.backend,
                    "reason": inference.reason,
                }
            )

    return {
        "worker_id": registration.worker_id,
        "coordinator_url": resolved_url,
        "benchmark_score": benchmark.benchmark_score,
        "heartbeat_sent": True,
        "challenges_solved": solved,
        "ai_requests_handled": ai_requests_handled,
    }


def run_worker_loop(
    *,
    state_dir: str | Path,
    coordinator_url: str | None = None,
    interval_seconds: float | None = None,
    iterations: int | None = None,
    benchmark_scale: int | None = None,
) -> dict[str, Any]:
    """Run the worker loop.

    `iterations=None` means run forever. Tests and demos can pass a small number.
    """

    completed = 0
    last_result: dict[str, Any] | None = None
    config = load_worker_config(state_dir, required=False)
    resolved_interval = interval_seconds if interval_seconds is not None else (config.interval_seconds if config else 30.0)
    while iterations is None or completed < iterations:
        last_result = run_worker_once(
            state_dir=state_dir,
            coordinator_url=coordinator_url,
            benchmark_scale=benchmark_scale,
            request_challenge=True,
        )
        completed += 1
        if iterations is not None and completed >= iterations:
            break
        time.sleep(max(1.0, resolved_interval))
    return {
        "iterations": completed,
        "last_result": last_result,
    }
