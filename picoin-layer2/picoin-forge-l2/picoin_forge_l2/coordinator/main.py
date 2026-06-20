from __future__ import annotations

import asyncio
import hmac
import html
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from picoin_forge_l2.common.constants import DEFAULT_COORDINATOR_STATE_DIR, DEFAULT_EPOCH_REWARD_PI
from picoin_forge_l2.common.crypto import request_signing_payload, verify_message
from picoin_forge_l2.common.hashing import hash_json
from picoin_forge_l2.common.models import (
    AIChatMessageCreateRequest,
    AIChatSessionCreateRequest,
    AIInferenceClaimRequest,
    AIInferenceCreateRequest,
    AIInferenceResult,
    BenchmarkResult,
    ChallengeCreateRequest,
    ChallengeResult,
    Heartbeat,
    WorkloadClaimRequest,
    WorkloadCreateRequest,
    WorkloadResult,
    WorkerRegistration,
)

from .ai_access_queue import (
    AIAccessQueue,
    ai_access_min_stake_pi,
    ai_inference_receipt_payload,
    ai_request_lease_seconds,
    ai_request_max_assignments,
    worker_can_serve_ai,
)
from .ai_chat import AIChatManager
from .challenge_engine import ChallengeEngine
from .calibration import build_benchmark_calibration_report, build_calibration_session, write_calibration_session
from .audit import build_event_export, write_event_export
from .demo import run_demo_network
from .epoch_engine import EpochEngine
from .federation import run_federated_demo, verify_federated_manifest
from .local_devnet import run_local_ai_devnet, run_local_ai_http_devnet
from .maintenance import challenge_expiration_loop, expire_challenges_once
from .settlement import build_settlement_payload_preview, list_settlements, read_settlement, summarize_settlement
from .storage import CoordinatorStorage, benchmark_normalization_caps
from .verifier import verify_settlement_file
from .worker_registry import WorkerRegistry
from .workload_queue import WorkloadQueue

try:  # pragma: no cover - exercised when optional CLI deps are installed.
    import typer
    from rich.console import Console
except ModuleNotFoundError:  # pragma: no cover
    typer = None
    Console = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    interval = float(os.getenv("PICOIN_FORGE_CHALLENGE_EXPIRER_SECONDS", "0") or "0")
    task = None
    if interval > 0:
        task = asyncio.create_task(
            challenge_expiration_loop(
                DEFAULT_COORDINATOR_STATE_DIR,
                interval_seconds=interval,
                on_error=lambda exc: CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR).record_event(
                    "maintenance.error",
                    "challenge_expirer",
                    {"error": str(exc)},
                ),
            )
        )
        app.state.challenge_expirer_task = task
        CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR).record_event(
            "maintenance.started",
            "challenge_expirer",
            {"interval_seconds": interval},
        )
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


api = FastAPI(title="Picoin Forge L2 Coordinator", version="0.1.0", lifespan=lifespan)


def registry(state_dir: str | Path | None = None) -> WorkerRegistry:
    return WorkerRegistry(state_dir or DEFAULT_COORDINATOR_STATE_DIR)


def challenges(state_dir: str | Path | None = None) -> ChallengeEngine:
    reg = registry(state_dir)
    return ChallengeEngine(state_dir or DEFAULT_COORDINATOR_STATE_DIR, reg)


def workloads(state_dir: str | Path | None = None) -> WorkloadQueue:
    return WorkloadQueue(state_dir or DEFAULT_COORDINATOR_STATE_DIR)


def ai_requests(state_dir: str | Path | None = None) -> AIAccessQueue:
    resolved = state_dir or DEFAULT_COORDINATOR_STATE_DIR
    return AIAccessQueue(resolved, registry(resolved))


def ai_chats(state_dir: str | Path | None = None) -> AIChatManager:
    resolved = state_dir or DEFAULT_COORDINATOR_STATE_DIR
    return AIChatManager(resolved, ai_requests(resolved))


def build_ai_capabilities(state_dir: str | Path | None = None) -> dict:
    ready_workers = [state for state in registry(state_dir).all() if worker_can_serve_ai(state)]
    capabilities: dict[str, int] = {}
    providers: dict[str, int] = {}
    models: dict[str, dict] = {}
    max_parameter_count_b = 0.0
    max_context_tokens = 0
    for state in ready_workers:
        profile = state.registration.ai_model_profile
        if profile is None:
            continue
        providers[profile.provider] = providers.get(profile.provider, 0) + 1
        max_parameter_count_b = max(max_parameter_count_b, profile.parameter_count_b)
        max_context_tokens = max(max_context_tokens, profile.context_tokens)
        for capability in profile.capabilities:
            capabilities[capability] = capabilities.get(capability, 0) + 1
        model_key = f"{profile.provider}:{profile.model_name}"
        current = models.setdefault(
            model_key,
            {
                "provider": profile.provider,
                "model_name": profile.model_name,
                "worker_count": 0,
                "max_parameter_count_b": profile.parameter_count_b,
                "max_context_tokens": profile.context_tokens,
                "capabilities": sorted(set(profile.capabilities)),
            },
        )
        current["worker_count"] += 1
        current["max_parameter_count_b"] = max(current["max_parameter_count_b"], profile.parameter_count_b)
        current["max_context_tokens"] = max(current["max_context_tokens"], profile.context_tokens)
        current["capabilities"] = sorted(set(current["capabilities"]) | set(profile.capabilities))
    return {
        "ready_worker_count": len(ready_workers),
        "providers": dict(sorted(providers.items())),
        "capabilities": dict(sorted(capabilities.items())),
        "models": [models[key] for key in sorted(models)],
        "max_parameter_count_b": max_parameter_count_b,
        "max_context_tokens": max_context_tokens,
        "queue_policy": "stake_priority_then_created_at",
        "no_per_task_payment": True,
    }


def build_ai_request_export(
    request_id: str,
    *,
    state_dir: str | Path | None = None,
    include_content: bool = False,
) -> dict:
    item = ai_requests(state_dir).get(request_id)
    receipt_payload = ai_inference_receipt_payload(item) if item.receipt_hash else None
    export_payload = {
        "schema": "picoin-forge-ai-request-export-v1",
        "request_id": item.request_id,
        "status": item.status.value,
        "requester_wallet": item.requester_wallet,
        "stake_snapshot_pi": item.stake_snapshot_pi,
        "required_stake_pi": item.required_stake_pi,
        "required_capabilities": item.required_capabilities,
        "model_hint": item.model_hint,
        "min_parameter_count_b": item.min_parameter_count_b,
        "min_context_tokens": item.min_context_tokens,
        "preferred_provider": item.preferred_provider,
        "max_tokens": item.max_tokens,
        "store_output": item.store_output,
        "assigned_worker_id": item.assigned_worker_id,
        "assignment_attempts": item.assignment_attempts,
        "assignment_history": item.assignment_history,
        "prompt_hash": item.prompt_hash,
        "output_hash": item.output_hash,
        "receipt_hash": item.receipt_hash,
        "receipt_payload_hash": hash_json(receipt_payload) if receipt_payload else None,
        "model_profile": item.model_profile.model_dump(mode="json") if item.model_profile else None,
        "failure_reason": item.failure_reason,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "no_l1_transaction_created": item.no_l1_transaction_created,
        "no_per_task_payment": item.no_per_task_payment,
    }
    if include_content:
        export_payload["prompt"] = item.prompt
        export_payload["output"] = item.output
    return {
        "request_id": request_id,
        "include_content": include_content,
        "export_hash": hash_json(export_payload),
        "export": export_payload,
    }


def require_write_token(x_picoin_forge_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("PICOIN_FORGE_COORDINATOR_TOKEN", "")
    if not expected:
        return
    if x_picoin_forge_token and hmac.compare_digest(x_picoin_forge_token, expected):
        return
    raise HTTPException(status_code=401, detail="missing or invalid coordinator token")


async def require_worker_submission(
    request: Request,
    x_picoin_forge_token: str | None = Header(default=None),
    x_picoin_forge_worker_id: str | None = Header(default=None),
    x_picoin_forge_timestamp: str | None = Header(default=None),
    x_picoin_forge_signature: str | None = Header(default=None),
) -> None:
    require_write_token(x_picoin_forge_token)
    signature_required = os.getenv("PICOIN_FORGE_REQUIRE_WORKER_SIGNATURES", "").lower() in {"1", "true", "yes"}
    signature_present = any([x_picoin_forge_worker_id, x_picoin_forge_timestamp, x_picoin_forge_signature])
    if not signature_required and not signature_present:
        return
    if not x_picoin_forge_worker_id or not x_picoin_forge_timestamp or not x_picoin_forge_signature:
        raise HTTPException(status_code=401, detail="missing worker signature headers")
    try:
        timestamp = float(x_picoin_forge_timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid worker signature timestamp") from exc
    if abs(time.time() - timestamp) > 300:
        raise HTTPException(status_code=401, detail="stale worker signature timestamp")
    body = await request.body()
    body_payload = json.loads(body.decode("utf-8") or "{}")
    body_worker_id = body_payload.get("worker_id")
    if body_worker_id and body_worker_id != x_picoin_forge_worker_id:
        raise HTTPException(status_code=401, detail="worker signature id mismatch")
    if request.url.path == "/workers/register":
        public_key = body_payload.get("public_key")
    else:
        try:
            public_key = registry().get(x_picoin_forge_worker_id).registration.public_key
        except KeyError as exc:
            raise HTTPException(status_code=401, detail="worker is not registered") from exc
    if not public_key or not verify_message(
        public_key,
        request_signing_payload(request.method, request.url.path, x_picoin_forge_timestamp, body),
        x_picoin_forge_signature,
    ):
        raise HTTPException(status_code=401, detail="invalid worker signature")
    signature_recorded = CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR).record_worker_signature_once(
        worker_id=x_picoin_forge_worker_id,
        signature=x_picoin_forge_signature,
        request_timestamp=x_picoin_forge_timestamp,
    )
    if not signature_recorded:
        raise HTTPException(status_code=401, detail="replayed worker signature")


@api.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "picoin-forge-l2-coordinator"}


@api.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    workers = registry().all()
    storage = CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR)
    events = storage.list_events(limit=20)
    benchmark_metrics = storage.list_benchmark_metrics(limit=10)
    challenge_metrics = storage.list_challenge_metrics(limit=1000)
    benchmark_metric_count = len(storage.list_benchmark_metrics(limit=1000))
    challenge_metric_count = len(challenge_metrics)
    settlements = list_settlements(DEFAULT_COORDINATOR_STATE_DIR, limit=10)
    ai_queue = ai_requests()
    ai_items = ai_queue.list(limit=20)
    ai_min_stake = ai_access_min_stake_pi()
    ai_worker_states = [state for state in workers if state.registration.ai_model_profile is not None]
    ai_ready_workers = [state for state in ai_worker_states if worker_can_serve_ai(state)]
    ai_status_counts: dict[str, int] = {}
    for item in ai_items:
        ai_status_counts[item.status.value] = ai_status_counts.get(item.status.value, 0) + 1
    max_worker_score = max((state.verified_compute_score for state in workers), default=0.0)
    passed_challenges = sum(1 for metric in challenge_metrics if metric["passed"])
    failed_challenges = challenge_metric_count - passed_challenges
    rows = "\n".join(
        f"<tr><td>{state.registration.worker_id}</td><td>{state.registration.wallet}</td>"
        f"<td>{state.registration.status.value}</td><td>{state.verified_compute_score:.4f}</td>"
        f"<td><div class=\"bar score\"><span style=\"width:{bar_width(state.verified_compute_score, max_worker_score)}%\"></span></div></td>"
        f"<td>{state.uptime_score:.2f}</td><td>{state.reliability_score:.2f}</td>"
        f"<td>{state.penalty_score:.2f}</td></tr>"
        for state in workers
    )
    ai_worker_rows = "\n".join(
        f"<tr><td>{escape_html(state.registration.worker_id)}</td>"
        f"<td>{escape_html(state.registration.ai_model_profile.model_name or '')}</td>"
        f"<td>{escape_html(state.registration.ai_model_profile.provider)}</td>"
        f"<td>{state.registration.ai_model_profile.parameter_count_b:.2f}B</td>"
        f"<td>{state.registration.ai_model_profile.context_tokens}</td>"
        f"<td>{escape_html(', '.join(state.registration.ai_model_profile.capabilities))}</td>"
        f"<td>{state.ai_model_score:.4f}</td>"
        f"<td>{'yes' if worker_can_serve_ai(state) else 'no'}</td></tr>"
        for state in ai_worker_states
        if state.registration.ai_model_profile is not None
    )
    ai_request_rows = "\n".join(render_ai_request_row(ai_queue, item) for item in ai_items)
    event_rows = "\n".join(
        f"<li><code>{event.event_type}</code> {event.subject_id or ''} <small>{event.created_at}</small></li>"
        for event in events
    )
    benchmark_rows = "\n".join(
        f"<tr><td>{metric['worker_id']}</td><td>{metric['normalized_score']:.4f}</td>"
        f"<td><div class=\"bar benchmark\"><span style=\"width:{bar_width(metric['normalized_score'], 100.0)}%\"></span></div></td>"
        f"<td><code>{metric['result_hash'][:16]}</code></td><td>{metric['created_at']}</td></tr>"
        for metric in benchmark_metrics
    )
    max_compute = max((settlement.total_verified_compute for settlement in settlements), default=0.0)
    epoch_rows = "\n".join(
        f"<tr><td>{settlement.epoch_id}</td><td>{settlement.worker_count if hasattr(settlement, 'worker_count') else len(settlement.workers)}</td>"
        f"<td>{settlement.total_verified_compute:.4f}</td><td>{settlement.epoch_reward:.6f}</td>"
        f"<td><div class=\"bar\"><span style=\"width:{bar_width(settlement.total_verified_compute, max_compute)}%\"></span></div></td>"
        f"<td><code>{settlement.result_hash[:16]}</code></td></tr>"
        for settlement in settlements
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Picoin Forge L2 Coordinator</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 32px; color: #17202a; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
    th, td {{ border: 1px solid #d6dbdf; padding: 8px; text-align: left; }}
    th {{ background: #f4f6f7; }}
    code {{ background: #f4f6f7; padding: 2px 4px; border-radius: 4px; }}
    .bar {{ background: #edf2f7; height: 10px; width: 100%; }}
    .bar span {{ display: block; background: #2b6cb0; height: 10px; }}
    .bar.score span {{ background: #2f855a; }}
    .bar.benchmark span {{ background: #805ad5; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 16px 0; }}
    .metric {{ border: 1px solid #d6dbdf; padding: 12px; }}
    .metric strong {{ display: block; font-size: 1.35rem; margin-top: 4px; }}
    .pill {{ display: inline-block; padding: 2px 6px; background: #eef2ff; border: 1px solid #c7d2fe; margin-right: 4px; }}
  </style>
</head>
<body>
  <h1>Picoin Forge L2 Coordinator</h1>
  <p>
    Workers: <strong>{len(workers)}</strong> |
    Settlements: <strong>{len(settlements)}</strong> |
    AI workers ready: <strong>{len(ai_ready_workers)}</strong> |
    AI min stake: <strong>{ai_min_stake:.6f} PI</strong> |
    AI queue: <strong>stake priority</strong> |
    AI lease: <strong>{ai_request_lease_seconds()}s/{ai_request_max_assignments()} attempts</strong> |
    Benchmark metrics: <strong>{benchmark_metric_count}</strong> |
    Challenge metrics: <strong>{challenge_metric_count}</strong>
  </p>
  <h2>Workers</h2>
  <table>
    <thead><tr><th>Worker</th><th>Wallet</th><th>Status</th><th>Score</th><th>Score Bar</th><th>Uptime</th><th>Reliability</th><th>Penalty</th></tr></thead>
    <tbody>{rows or '<tr><td colspan="8">No workers registered.</td></tr>'}</tbody>
  </table>
  <h2>Metrics Charts</h2>
  <div class="metric-grid">
    <div class="metric">Challenge Passes<strong>{passed_challenges}</strong></div>
    <div class="metric">Challenge Failures<strong>{failed_challenges}</strong></div>
    <div class="metric">Latest Benchmarks<strong>{len(benchmark_metrics)}</strong></div>
    <div class="metric">AI Requests<strong>{len(ai_items)}</strong></div>
  </div>
  <h2>AI Network</h2>
  <p>
    <span class="pill">queued {ai_status_counts.get('queued', 0)}</span>
    <span class="pill">assigned {ai_status_counts.get('assigned', 0)}</span>
    <span class="pill">verified {ai_status_counts.get('verified', 0)}</span>
    <span class="pill">failed {ai_status_counts.get('failed', 0)}</span>
  </p>
  <h3>AI Workers</h3>
  <table>
    <thead><tr><th>Worker</th><th>Model</th><th>Provider</th><th>Params</th><th>Context</th><th>Capabilities</th><th>AI Score</th><th>Ready</th></tr></thead>
    <tbody>{ai_worker_rows or '<tr><td colspan="8">No AI model workers registered.</td></tr>'}</tbody>
  </table>
  <h3>Latest AI Requests</h3>
  <table>
    <thead><tr><th>Request</th><th>Status</th><th>Requester</th><th>Stake Snapshot</th><th>Worker</th><th>Suggested Worker</th><th>Attempts</th><th>Lease Expires</th><th>Failure</th><th>Prompt Hash</th><th>Receipt</th><th>Capabilities</th></tr></thead>
    <tbody>{ai_request_rows or '<tr><td colspan="12">No AI requests yet.</td></tr>'}</tbody>
  </table>
  <h3>Latest Benchmark Metrics</h3>
  <table>
    <thead><tr><th>Worker</th><th>Normalized Score</th><th>Score Bar</th><th>Hash</th><th>Recorded</th></tr></thead>
    <tbody>{benchmark_rows or '<tr><td colspan="5">No benchmark metrics yet.</td></tr>'}</tbody>
  </table>
  <h2>Epoch History</h2>
  <table>
    <thead><tr><th>Epoch</th><th>Workers</th><th>Total Compute</th><th>Reward</th><th>Compute Bar</th><th>Hash</th></tr></thead>
    <tbody>{epoch_rows or '<tr><td colspan="6">No settlements yet.</td></tr>'}</tbody>
  </table>
  <h2>Recent Events</h2>
  <ul>{event_rows or '<li>No events yet.</li>'}</ul>
</body>
</html>"""


@api.get("/ai/portal", response_class=HTMLResponse)
def ai_portal() -> str:
    return render_ai_portal(ai_access_min_stake_pi())


@api.get("/events")
def events_api(limit: int = 100) -> list[dict]:
    events = CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR).list_events(limit=limit)
    return [event.model_dump(mode="json") for event in events]


@api.get("/events/export")
def events_export_api(limit: int = 1000) -> dict:
    return build_event_export(DEFAULT_COORDINATOR_STATE_DIR, limit=limit)


@api.get("/epochs")
def epochs_api(limit: int = 100) -> list[dict]:
    return [summarize_settlement(settlement) for settlement in list_settlements(DEFAULT_COORDINATOR_STATE_DIR, limit=limit)]


@api.get("/epochs/{epoch_id}")
def epoch_detail_api(epoch_id: int) -> dict:
    try:
        return read_settlement(DEFAULT_COORDINATOR_STATE_DIR, epoch_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.get("/epochs/{epoch_id}/l1-preview")
def epoch_l1_preview_api(epoch_id: int) -> dict:
    try:
        settlement = read_settlement(DEFAULT_COORDINATOR_STATE_DIR, epoch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return build_settlement_payload_preview(settlement).model_dump(mode="json")


@api.get("/metrics/benchmarks")
def benchmark_metrics_api(limit: int = 100, worker_id: str | None = None) -> list[dict]:
    return CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR).list_benchmark_metrics(worker_id=worker_id, limit=limit)


@api.get("/metrics/config")
def metrics_config_api() -> dict:
    return {
        "benchmark_normalization_caps": benchmark_normalization_caps(),
    }


@api.get("/metrics/calibration")
def metrics_calibration_api(limit: int = 1000, percentile: float = 0.95) -> dict:
    metrics = CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR).list_benchmark_metrics(limit=limit)
    return build_benchmark_calibration_report(metrics, percentile=percentile)


@api.get("/metrics/calibration/session")
def metrics_calibration_session_api(limit: int = 1000, percentile: float = 0.95) -> dict:
    metrics = CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR).list_benchmark_metrics(limit=limit)
    return build_calibration_session(metrics, percentile=percentile)


@api.get("/metrics/challenges")
def challenge_metrics_api(limit: int = 100, worker_id: str | None = None) -> list[dict]:
    return CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR).list_challenge_metrics(worker_id=worker_id, limit=limit)


@api.post("/workers/register")
def register_worker_api(registration: WorkerRegistration, _: None = Depends(require_worker_submission)) -> dict:
    state = registry().register(registration)
    return state.model_dump(mode="json")


@api.get("/workers")
def workers_api() -> list[dict]:
    return [state.model_dump(mode="json") for state in registry().all()]


@api.get("/workers/{worker_id}/metrics")
def worker_metrics_api(worker_id: str, limit: int = 100) -> dict:
    storage = CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR)
    return {
        "worker_id": worker_id,
        "benchmark_metrics": storage.list_benchmark_metrics(worker_id=worker_id, limit=limit),
        "challenge_metrics": storage.list_challenge_metrics(worker_id=worker_id, limit=limit),
    }


@api.post("/workloads")
def create_workload_api(payload: WorkloadCreateRequest, _: None = Depends(require_write_token)) -> dict:
    return workloads().create(payload).model_dump(mode="json")


@api.get("/workloads")
def workloads_api(limit: int = 100) -> list[dict]:
    return [task.model_dump(mode="json") for task in workloads().list(limit=limit)]


@api.post("/workloads/claim")
def claim_workload_api(payload: WorkloadClaimRequest, _: None = Depends(require_worker_submission)) -> dict:
    task = workloads().claim_next(payload.worker_id)
    return {"task": task.model_dump(mode="json") if task else None}


@api.post("/workloads/{task_id}/submit")
def submit_workload_api(
    task_id: str,
    result: WorkloadResult,
    _: None = Depends(require_worker_submission),
) -> dict:
    if result.task_id != task_id:
        return {"accepted": False, "reason": "task_id mismatch"}
    task = workloads().submit(result)
    return {"accepted": task.status.value == "verified", "task": task.model_dump(mode="json")}


@api.post("/ai/requests")
def create_ai_request_api(payload: AIInferenceCreateRequest, _: None = Depends(require_write_token)) -> dict:
    try:
        request_item = ai_requests().create(payload)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return request_item.model_dump(mode="json")


@api.get("/ai/requests")
def ai_requests_api(limit: int = 100, requester_wallet: str | None = None) -> list[dict]:
    return [
        item.model_dump(mode="json")
        for item in ai_requests().list(limit=limit, requester_wallet=requester_wallet)
    ]


@api.get("/ai/capabilities")
def ai_capabilities_api() -> dict:
    return build_ai_capabilities(DEFAULT_COORDINATOR_STATE_DIR)


@api.post("/ai/chat/sessions")
def create_ai_chat_session_api(payload: AIChatSessionCreateRequest, _: None = Depends(require_write_token)) -> dict:
    return ai_chats().create_session(payload).model_dump(mode="json")


@api.get("/ai/chat/sessions")
def ai_chat_sessions_api(limit: int = 100, requester_wallet: str | None = None) -> list[dict]:
    return [
        session.model_dump(mode="json")
        for session in ai_chats().list_sessions(limit=limit, requester_wallet=requester_wallet)
    ]


@api.get("/ai/chat/sessions/{session_id}")
def ai_chat_session_api(session_id: str) -> dict:
    try:
        return ai_chats().get_session(session_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.get("/ai/chat/sessions/{session_id}/messages")
def ai_chat_messages_api(session_id: str) -> list[dict]:
    try:
        return [message.model_dump(mode="json") for message in ai_chats().list_messages(session_id)]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/ai/chat/sessions/{session_id}/messages")
def create_ai_chat_message_api(
    session_id: str,
    payload: AIChatMessageCreateRequest,
    _: None = Depends(require_write_token),
) -> dict:
    try:
        result = ai_chats().send_message(session_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "session": result["session"].model_dump(mode="json"),
        "message": result["message"].model_dump(mode="json"),
        "ai_request": result["ai_request"].model_dump(mode="json"),
    }


@api.post("/ai/chat/sessions/{session_id}/sync")
def sync_ai_chat_message_api(
    session_id: str,
    request_id: str,
    _: None = Depends(require_write_token),
) -> dict:
    try:
        result = ai_chats().sync_request(session_id, request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "synced": result["synced"],
        "request_status": result["request_status"],
        "session": result["session"].model_dump(mode="json"),
        "request_message": result["request_message"].model_dump(mode="json"),
        "assistant_message": result["assistant_message"].model_dump(mode="json")
        if result["assistant_message"]
        else None,
        "ai_request": result["ai_request"].model_dump(mode="json"),
    }


@api.get("/ai/requests/{request_id}/routing")
def ai_request_routing_api(request_id: str, limit: int = 10) -> dict:
    queue = ai_requests()
    queue.release_expired_assignments()
    try:
        item = queue.get(request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    safe_limit = max(1, min(int(limit), 100))
    candidates = queue.candidate_workers_for_request(item)[:safe_limit]
    return {
        "request_id": request_id,
        "status": item.status.value,
        "selected_worker_id": candidates[0]["worker_id"] if candidates else None,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


@api.get("/ai/requests/{request_id}/status")
def ai_request_status_api(request_id: str) -> dict:
    queue = ai_requests()
    queue.release_expired_assignments()
    try:
        item = queue.get(request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "request_id": item.request_id,
        "status": item.status.value,
        "requester_wallet": item.requester_wallet,
        "stake_snapshot_pi": item.stake_snapshot_pi,
        "required_stake_pi": item.required_stake_pi,
        "assigned_worker_id": item.assigned_worker_id,
        "assigned_at": item.assigned_at.isoformat() if item.assigned_at else None,
        "lease_expires_at": item.lease_expires_at.isoformat() if item.lease_expires_at else None,
        "assignment_attempts": item.assignment_attempts,
        "assignment_history": item.assignment_history,
        "failure_reason": item.failure_reason,
        "result_ready": item.status.value == "verified" and item.output_hash is not None,
        "output_stored": item.output is not None,
        "receipt_ready": item.receipt_hash is not None,
        "output_hash": item.output_hash,
        "receipt_hash": item.receipt_hash,
        "queue_policy": "stake_priority_then_created_at",
        "no_l1_transaction_created": item.no_l1_transaction_created,
        "no_per_task_payment": item.no_per_task_payment,
    }


@api.get("/ai/requests/{request_id}/receipt")
def ai_request_receipt_api(request_id: str) -> dict:
    try:
        item = ai_requests().get(request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not item.receipt_hash:
        raise HTTPException(status_code=404, detail="AI request does not have a receipt yet")
    payload = ai_inference_receipt_payload(item)
    computed_hash = hash_json(payload)
    return {
        "request_id": request_id,
        "receipt_hash": item.receipt_hash,
        "computed_receipt_hash": computed_hash,
        "valid": hmac.compare_digest(item.receipt_hash, computed_hash),
        "payload": payload,
    }


@api.get("/ai/requests/{request_id}/result")
def ai_request_result_api(request_id: str) -> dict:
    try:
        item = ai_requests().get(request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if item.status.value != "verified" or item.output_hash is None:
        raise HTTPException(status_code=404, detail="AI request result is not available yet")
    return {
        "request_id": request_id,
        "status": item.status.value,
        "requester_wallet": item.requester_wallet,
        "assigned_worker_id": item.assigned_worker_id,
        "model_profile": item.model_profile.model_dump(mode="json") if item.model_profile else None,
        "output": item.output,
        "output_stored": item.output is not None,
        "output_hash": item.output_hash,
        "receipt_hash": item.receipt_hash,
        "no_l1_transaction_created": item.no_l1_transaction_created,
        "no_per_task_payment": item.no_per_task_payment,
    }


@api.get("/ai/requests/{request_id}/export")
def ai_request_export_api(request_id: str, include_content: bool = False) -> dict:
    try:
        return build_ai_request_export(
            request_id,
            state_dir=DEFAULT_COORDINATOR_STATE_DIR,
            include_content=include_content,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.get("/ai/summary")
def ai_summary_api(limit: int = 100) -> dict:
    workers = registry().all()
    queue = ai_requests()
    queue.release_expired_assignments()
    items = queue.list(limit=limit)
    status_counts: dict[str, int] = {}
    for item in items:
        status_counts[item.status.value] = status_counts.get(item.status.value, 0) + 1
    ai_workers = []
    for state in workers:
        profile = state.registration.ai_model_profile
        if profile is None:
            continue
        ai_workers.append(
            {
                "worker_id": state.registration.worker_id,
                "wallet": state.registration.wallet,
                "model_name": profile.model_name,
                "provider": profile.provider,
                "parameter_count_b": profile.parameter_count_b,
                "context_tokens": profile.context_tokens,
                "capabilities": profile.capabilities,
                "ai_model_score": state.ai_model_score,
                "ready": worker_can_serve_ai(state),
            }
        )
    return {
        "min_stake_pi": ai_access_min_stake_pi(),
        "queue_policy": "stake_priority_then_created_at",
        "request_lease_seconds": ai_request_lease_seconds(),
        "request_max_assignments": ai_request_max_assignments(),
        "ai_workers_total": len(ai_workers),
        "ai_workers_ready": sum(1 for worker in ai_workers if worker["ready"]),
        "request_status_counts": status_counts,
        "latest_request_count": len(items),
        "workers": ai_workers,
    }


@api.post("/ai/requests/expire")
def expire_ai_requests_api(_: None = Depends(require_write_token)) -> dict:
    expired = ai_requests().release_expired_assignments()
    return {
        "expired": len(expired),
        "requests": [item.model_dump(mode="json") for item in expired],
    }


@api.post("/ai/requests/claim")
def claim_ai_request_api(payload: AIInferenceClaimRequest, _: None = Depends(require_worker_submission)) -> dict:
    request_item = ai_requests().claim_next(payload.worker_id)
    return {"request": request_item.model_dump(mode="json") if request_item else None}


@api.post("/ai/requests/{request_id}/cancel")
def cancel_ai_request_api(request_id: str, _: None = Depends(require_write_token)) -> dict:
    try:
        request_item = ai_requests().cancel(request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"canceled": request_item.status.value == "canceled", "request": request_item.model_dump(mode="json")}


@api.post("/ai/requests/{request_id}/submit")
def submit_ai_request_api(
    request_id: str,
    result: AIInferenceResult,
    _: None = Depends(require_worker_submission),
) -> dict:
    if result.request_id != request_id:
        return {"accepted": False, "reason": "request_id mismatch"}
    request_item = ai_requests().submit(result)
    return {"accepted": request_item.status.value == "verified", "request": request_item.model_dump(mode="json")}


@api.post("/benchmarks")
def benchmark_api(benchmark: BenchmarkResult, _: None = Depends(require_worker_submission)) -> dict:
    state = registry().update_benchmark(benchmark)
    return state.model_dump(mode="json")


@api.post("/heartbeats")
def heartbeat_api(heartbeat: Heartbeat, _: None = Depends(require_worker_submission)) -> dict:
    state = registry().heartbeat(heartbeat)
    return state.model_dump(mode="json")


@api.post("/challenges")
def create_challenge_api(payload: ChallengeCreateRequest, _: None = Depends(require_worker_submission)) -> dict:
    challenge = challenges().create_challenge(
        payload.worker_id,
        payload.challenge_type,
        payload.difficulty,
    )
    return challenge.model_dump(mode="json")


@api.get("/challenges/{challenge_id}")
def get_challenge_api(challenge_id: str) -> dict:
    return challenges().get(challenge_id).model_dump(mode="json")


@api.get("/workers/{worker_id}/challenges")
def worker_challenges_api(worker_id: str, open_only: bool = False) -> list[dict]:
    engine = challenges()
    rows = engine.list_open_for_worker(worker_id) if open_only else engine.list_for_worker(worker_id)
    return [row.model_dump(mode="json") for row in rows]


@api.post("/challenges/{challenge_id}/submit")
def submit_challenge_api(
    challenge_id: str,
    result: ChallengeResult,
    _: None = Depends(require_worker_submission),
) -> dict:
    if result.challenge_id != challenge_id:
        return {"accepted": False, "reason": "challenge_id mismatch"}
    passed = challenges().verify_result(result)
    return {"accepted": passed, "challenge_id": challenge_id}


@api.post("/challenges/expire")
def expire_challenges_api(_: None = Depends(require_write_token)) -> dict:
    expired = challenges().expire_open_challenges()
    return {
        "expired": len(expired),
        "challenges": [challenge.model_dump(mode="json") for challenge in expired],
    }


@api.post("/epochs/close")
def close_epoch_api(epoch_reward: float = DEFAULT_EPOCH_REWARD_PI, _: None = Depends(require_write_token)) -> dict:
    challenges().expire_open_challenges()
    settlement = EpochEngine(DEFAULT_COORDINATOR_STATE_DIR, registry()).close_epoch(epoch_reward)
    return settlement.model_dump(mode="json")


if typer is not None:
    app = typer.Typer(help="Picoin Forge L2 coordinator CLI.")
    epoch_app = typer.Typer(help="Epoch commands.")
    ai_app = typer.Typer(help="AI access commands.")
    app.add_typer(epoch_app, name="epoch")
    app.add_typer(ai_app, name="ai")
    console = Console()

    @app.command()
    def start(host: str = "127.0.0.1", port: int = 9380) -> None:
        import uvicorn

        uvicorn.run(api, host=host, port=port)

    @app.command()
    def workers(state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR)) -> None:
        rows = [state.model_dump(mode="json") for state in registry(state_dir).all()]
        console.print_json(data=rows)

    @epoch_app.command("close")
    def epoch_close(
        state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR),
        epoch_reward: float = DEFAULT_EPOCH_REWARD_PI,
    ) -> None:
        settlement = EpochEngine(state_dir, registry(state_dir)).close_epoch(epoch_reward)
        console.print_json(data=settlement.model_dump(mode="json"))

    @app.command()
    def rewards(state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR)) -> None:
        settlements = sorted(Path(state_dir).glob("settlement_epoch_*.json"))
        if not settlements:
            console.print_json(data=[])
            return
        console.print(settlements[-1].read_text(encoding="utf-8"))

    @app.command("l1-preview")
    def l1_preview(epoch_id: int, state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR)) -> None:
        preview = build_settlement_payload_preview(read_settlement(state_dir, epoch_id))
        console.print_json(data=preview.model_dump(mode="json"))

    @app.command("verify-settlement")
    def verify_settlement_command(epoch_id: int, state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR)) -> None:
        console.print_json(data=verify_settlement_file(state_dir, epoch_id))

    @app.command()
    def events(state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR), limit: int = 20) -> None:
        rows = [event.model_dump(mode="json") for event in CoordinatorStorage(state_dir).list_events(limit=limit)]
        console.print_json(data=rows)

    @app.command("export-events")
    def export_events(
        output_path: Path,
        state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR),
        limit: int = 1000,
    ) -> None:
        console.print_json(data=write_event_export(state_dir, output_path, limit=limit))

    @app.command("metrics-calibration")
    def metrics_calibration(
        state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR),
        limit: int = 1000,
        percentile: float = 0.95,
    ) -> None:
        metrics = CoordinatorStorage(state_dir).list_benchmark_metrics(limit=limit)
        console.print_json(data=build_benchmark_calibration_report(metrics, percentile=percentile))

    @app.command("metrics-calibration-session")
    def metrics_calibration_session(
        output_dir: Path,
        state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR),
        limit: int = 1000,
        percentile: float = 0.95,
    ) -> None:
        console.print_json(data=write_calibration_session(state_dir, output_dir, limit=limit, percentile=percentile))

    @app.command("expire-challenges")
    def expire_challenges(state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR)) -> None:
        console.print_json(data=expire_challenges_once(state_dir))

    @ai_app.command("capabilities")
    def ai_capabilities(state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR)) -> None:
        console.print_json(data=build_ai_capabilities(state_dir))

    @ai_app.command("create-request")
    def ai_create_request(
        requester_wallet: str,
        prompt: str,
        stake_snapshot_pi: float,
        capabilities: str = "chat",
        state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR),
        max_tokens: int = 256,
        model_hint: str | None = None,
        min_parameter_count_b: float = 0.0,
        min_context_tokens: int = 0,
        preferred_provider: str | None = None,
        store_output: bool = True,
    ) -> None:
        request_item = AIAccessQueue(state_dir, registry(state_dir)).create(
            AIInferenceCreateRequest(
                requester_wallet=requester_wallet,
                stake_snapshot_pi=stake_snapshot_pi,
                prompt=prompt,
                required_capabilities=[item.strip() for item in capabilities.split(",") if item.strip()],
                model_hint=model_hint,
                min_parameter_count_b=min_parameter_count_b,
                min_context_tokens=min_context_tokens,
                preferred_provider=preferred_provider,
                max_tokens=max_tokens,
                store_output=store_output,
            )
        )
        console.print_json(data=request_item.model_dump(mode="json"))

    @ai_app.command("status")
    def ai_status(request_id: str, state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR)) -> None:
        queue = AIAccessQueue(state_dir, registry(state_dir))
        queue.release_expired_assignments()
        item = queue.get(request_id)
        console.print_json(
            data={
                "request_id": item.request_id,
                "status": item.status.value,
                "assigned_worker_id": item.assigned_worker_id,
                "assignment_attempts": item.assignment_attempts,
                "failure_reason": item.failure_reason,
                "result_ready": item.status.value == "verified" and item.output_hash is not None,
                "output_stored": item.output is not None,
                "receipt_ready": item.receipt_hash is not None,
                "receipt_hash": item.receipt_hash,
                "no_per_task_payment": item.no_per_task_payment,
            }
        )

    @ai_app.command("result")
    def ai_result(request_id: str, state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR)) -> None:
        item = AIAccessQueue(state_dir, registry(state_dir)).get(request_id)
        console.print_json(
            data={
                "request_id": item.request_id,
                "status": item.status.value,
                "output": item.output,
                "output_stored": item.output is not None,
                "output_hash": item.output_hash,
                "receipt_hash": item.receipt_hash,
                "no_per_task_payment": item.no_per_task_payment,
            }
        )

    @ai_app.command("receipt")
    def ai_receipt(request_id: str, state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR)) -> None:
        item = AIAccessQueue(state_dir, registry(state_dir)).get(request_id)
        payload = ai_inference_receipt_payload(item)
        computed_hash = hash_json(payload)
        console.print_json(
            data={
                "request_id": request_id,
                "receipt_hash": item.receipt_hash,
                "computed_receipt_hash": computed_hash,
                "valid": bool(item.receipt_hash and hmac.compare_digest(item.receipt_hash, computed_hash)),
                "payload": payload,
            }
        )

    @ai_app.command("export-request")
    def ai_export_request(
        request_id: str,
        state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR),
        include_content: bool = False,
    ) -> None:
        console.print_json(
            data=build_ai_request_export(
                request_id,
                state_dir=state_dir,
                include_content=include_content,
            )
        )

    @app.command()
    def demo(
        state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR),
        workers: int = 3,
        epoch_reward: float = DEFAULT_EPOCH_REWARD_PI,
    ) -> None:
        result = run_demo_network(state_dir, worker_count=workers, epoch_reward=epoch_reward)
        console.print_json(data=result.model_dump(mode="json"))

    @app.command("local-ai-demo")
    def local_ai_demo(
        state_dir: Path = Path(".picoin-forge-l2-local-demo"),
        workers: int = 2,
        requester_wallet: str = "PILOCALAIREQUESTER0001",
        stake_snapshot_pi: float = 25.0,
        prompt: str = "Explain Picoin Forge L2 in one short paragraph.",
        epoch_reward: float = DEFAULT_EPOCH_REWARD_PI,
    ) -> None:
        result = run_local_ai_devnet(
            state_dir,
            worker_count=workers,
            requester_wallet=requester_wallet,
            stake_snapshot_pi=stake_snapshot_pi,
            prompt=prompt,
            epoch_reward=epoch_reward,
        )
        console.print_json(data=result)

    @app.command("local-ai-http-demo")
    def local_ai_http_demo(
        state_dir: Path = Path(".picoin-forge-l2-local-http-demo"),
        workers: int = 1,
        requester_wallet: str = "PILOCALHTTPREQUESTER0001",
        stake_snapshot_pi: float = 25.0,
        prompt: str = "Explain Picoin Forge L2 HTTP devnet in one short paragraph.",
        epoch_reward: float = DEFAULT_EPOCH_REWARD_PI,
    ) -> None:
        result = run_local_ai_http_devnet(
            state_dir,
            worker_count=workers,
            requester_wallet=requester_wallet,
            stake_snapshot_pi=stake_snapshot_pi,
            prompt=prompt,
            epoch_reward=epoch_reward,
        )
        console.print_json(data=result)

    @app.command("federation-demo")
    def federation_demo(
        state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR),
        coordinators: int = 2,
        workers_per_coordinator: int = 2,
        epoch_reward: float = DEFAULT_EPOCH_REWARD_PI,
    ) -> None:
        result = run_federated_demo(
            state_dir,
            coordinator_count=coordinators,
            workers_per_coordinator=workers_per_coordinator,
            epoch_reward=epoch_reward,
        )
        console.print_json(data=result)

    @app.command("verify-federation")
    def verify_federation(manifest_path: Path) -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        console.print_json(data=verify_federated_manifest(manifest))
else:  # pragma: no cover
    app = None


def bar_width(value: float, max_value: float) -> float:
    if max_value <= 0:
        return 0.0
    return round(min(max(value / max_value, 0.0), 1.0) * 100.0, 2)


def escape_html(value: object) -> str:
    return html.escape(str(value), quote=True)


def render_ai_portal(min_stake_pi: float) -> str:
    return AI_PORTAL_HTML.replace("__MIN_STAKE_PI__", f"{min_stake_pi:.6f}")


def render_ai_request_row(queue: AIAccessQueue, item) -> str:
    selected = queue.select_worker_for_request(item)
    selected_worker_id = selected["worker_id"] if selected else ""
    lease_expires_at = item.lease_expires_at.isoformat() if item.lease_expires_at else ""
    return (
        f"<tr><td><code>{escape_html(item.request_id)}</code></td>"
        f"<td>{escape_html(item.status.value)}</td>"
        f"<td>{escape_html(item.requester_wallet)}</td>"
        f"<td>{item.stake_snapshot_pi:.6f}</td>"
        f"<td>{escape_html(item.assigned_worker_id or '')}</td>"
        f"<td>{escape_html(selected_worker_id)}</td>"
        f"<td>{item.assignment_attempts}</td>"
        f"<td>{escape_html(lease_expires_at)}</td>"
        f"<td>{escape_html(item.failure_reason or '')}</td>"
        f"<td><code>{escape_html(item.prompt_hash[:16])}</code></td>"
        f"<td><code>{escape_html((item.receipt_hash or '')[:16])}</code></td>"
        f"<td>{escape_html(', '.join(item.required_capabilities))}</td></tr>"
    )


AI_PORTAL_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Picoin Forge AI Portal</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #17202a;
      --muted: #5d6d7e;
      --line: #d6dbdf;
      --surface: #ffffff;
      --band: #f6f8fa;
      --accent: #1967d2;
      --ok: #18794e;
      --warn: #9a6700;
      --bad: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--band);
    }
    header {
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }
    h1 { font-size: 1.35rem; margin: 0; letter-spacing: 0; }
    main {
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px;
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
      gap: 20px;
    }
    section {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    h2 { font-size: 1rem; margin: 0 0 14px; letter-spacing: 0; }
    label {
      display: block;
      font-size: 0.82rem;
      color: var(--muted);
      margin-bottom: 6px;
    }
    input, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 11px;
      font: inherit;
      background: #fff;
      color: var(--ink);
    }
    textarea { min-height: 150px; resize: vertical; }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .field { margin-bottom: 12px; }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    button {
      border: 1px solid var(--accent);
      background: var(--accent);
      color: #fff;
      border-radius: 6px;
      padding: 10px 12px;
      font-weight: 650;
      cursor: pointer;
    }
    button.secondary {
      background: #fff;
      color: var(--accent);
    }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .status-line {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 9px;
      background: #fff;
      font-size: 0.84rem;
    }
    .pill.ok { color: var(--ok); border-color: #abefc6; background: #ecfdf3; }
    .pill.warn { color: var(--warn); border-color: #fedf89; background: #fffaeb; }
    .pill.bad { color: var(--bad); border-color: #fecdca; background: #fef3f2; }
    pre {
      margin: 0;
      padding: 12px;
      background: #0b1020;
      color: #e6edf3;
      border-radius: 8px;
      overflow: auto;
      min-height: 140px;
      font-size: 0.83rem;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .message-list {
      display: grid;
      gap: 10px;
      min-height: 220px;
    }
    .message {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      background: #fff;
      overflow-wrap: anywhere;
    }
    .message.user { border-color: #bfdbfe; background: #eff6ff; }
    .message.assistant { border-color: #bbf7d0; background: #f0fdf4; }
    .message strong {
      display: block;
      font-size: 0.78rem;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .stack { display: grid; gap: 16px; }
    .output-title {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      margin-bottom: 8px;
    }
    .small { color: var(--muted); font-size: 0.85rem; }
    a { color: var(--accent); text-decoration: none; }
    @media (max-width: 860px) {
      main { grid-template-columns: 1fr; padding: 16px; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Picoin Forge AI Portal</h1>
    <nav><a href="/">Coordinator</a></nav>
  </header>
  <main>
    <section>
      <h2>Chat Session</h2>
      <form id="request-form">
        <div class="field">
          <label for="requester">Requester wallet</label>
          <input id="requester" value="PI_REQUESTER" autocomplete="off">
        </div>
        <div class="field">
          <label for="session-title">Session title</label>
          <input id="session-title" value="Picoin Forge chat" autocomplete="off">
        </div>
        <div class="grid">
          <div class="field">
            <label for="stake">Stake snapshot PI</label>
            <input id="stake" type="number" min="0" step="0.000001" value="25">
          </div>
          <div class="field">
            <label for="max-tokens">Max tokens</label>
            <input id="max-tokens" type="number" min="1" max="4096" step="1" value="256">
          </div>
        </div>
        <div class="grid">
          <div class="field">
            <label for="capabilities">Capabilities</label>
            <input id="capabilities" value="chat,reasoning" autocomplete="off">
          </div>
          <div class="field">
            <label for="provider">Preferred provider</label>
            <input id="provider" placeholder="ollama" autocomplete="off">
          </div>
        </div>
        <div class="grid">
          <div class="field">
            <label for="model-hint">Model hint</label>
            <input id="model-hint" placeholder="llama" autocomplete="off">
          </div>
          <div class="field">
            <label for="token">Coordinator token</label>
            <input id="token" type="password" autocomplete="off">
          </div>
        </div>
        <div class="field">
          <label for="prompt">Prompt</label>
          <textarea id="prompt">Explain Picoin Forge L2 in one short paragraph.</textarea>
        </div>
        <div class="actions">
          <button id="submit" type="submit">Send</button>
          <button class="secondary" id="capabilities-button" type="button">Capabilities</button>
        </div>
      </form>
    </section>
    <div class="stack">
      <section>
        <div class="output-title">
          <h2>Chat</h2>
          <span id="session-id" class="pill">session</span>
        </div>
        <div id="chat-output" class="message-list"></div>
      </section>
      <section>
        <div class="output-title">
          <h2>Status</h2>
          <span class="small">Minimum stake __MIN_STAKE_PI__ PI</span>
        </div>
        <div class="status-line">
          <span id="state-pill" class="pill warn">idle</span>
          <span id="request-id" class="pill">request</span>
          <span id="worker-id" class="pill">worker</span>
        </div>
        <pre id="status-output">{}</pre>
      </section>
      <section>
        <div class="output-title"><h2>Result</h2></div>
        <pre id="result-output">{}</pre>
      </section>
      <section>
        <div class="output-title"><h2>Receipt</h2></div>
        <pre id="receipt-output">{}</pre>
      </section>
    </div>
  </main>
  <script>
    const statusOutput = document.getElementById("status-output");
    const resultOutput = document.getElementById("result-output");
    const receiptOutput = document.getElementById("receipt-output");
    const chatOutput = document.getElementById("chat-output");
    const statePill = document.getElementById("state-pill");
    const sessionIdPill = document.getElementById("session-id");
    const requestIdPill = document.getElementById("request-id");
    const workerIdPill = document.getElementById("worker-id");
    const submitButton = document.getElementById("submit");
    let activeSessionId = null;

    function tokenHeaders() {
      const token = document.getElementById("token").value.trim();
      return token ? {"X-Picoin-Forge-Token": token} : {};
    }

    function asCapabilities(value) {
      return value.split(",").map((item) => item.trim()).filter(Boolean);
    }

    function render(target, value) {
      target.textContent = JSON.stringify(value, null, 2);
    }

    function appendMessage(role, content, meta = "") {
      const row = document.createElement("div");
      row.className = `message ${role}`;
      const label = document.createElement("strong");
      label.textContent = meta ? `${role} · ${meta}` : role;
      const body = document.createElement("div");
      body.textContent = content || "";
      row.appendChild(label);
      row.appendChild(body);
      chatOutput.appendChild(row);
      chatOutput.scrollTop = chatOutput.scrollHeight;
    }

    function setState(status) {
      statePill.textContent = status || "idle";
      statePill.className = "pill";
      if (status === "verified") statePill.classList.add("ok");
      else if (status === "failed" || status === "canceled" || status === "error") statePill.classList.add("bad");
      else statePill.classList.add("warn");
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: {
          "Accept": "application/json",
          ...(options.body ? {"Content-Type": "application/json"} : {}),
          ...tokenHeaders(),
          ...(options.headers || {})
        }
      });
      const text = await response.text();
      const payload = text ? JSON.parse(text) : {};
      if (!response.ok) throw payload;
      return payload;
    }

    async function ensureSession() {
      if (activeSessionId) return activeSessionId;
      const session = await api("/ai/chat/sessions", {
        method: "POST",
        body: JSON.stringify({
          requester_wallet: document.getElementById("requester").value.trim(),
          stake_snapshot_pi: Number(document.getElementById("stake").value),
          title: document.getElementById("session-title").value.trim() || null,
          required_capabilities: asCapabilities(document.getElementById("capabilities").value),
          preferred_provider: document.getElementById("provider").value.trim() || null,
          model_hint: document.getElementById("model-hint").value.trim() || null,
          max_tokens: Number(document.getElementById("max-tokens").value),
          store_output: true
        })
      });
      activeSessionId = session.session_id;
      sessionIdPill.textContent = activeSessionId;
      render(statusOutput, session);
      return activeSessionId;
    }

    async function pollRequest(requestId) {
      for (let index = 0; index < 120; index += 1) {
        const status = await api(`/ai/requests/${encodeURIComponent(requestId)}/status`);
        render(statusOutput, status);
        setState(status.status);
        requestIdPill.textContent = status.request_id || "request";
        workerIdPill.textContent = status.assigned_worker_id || "worker";
        if (status.status === "verified") {
          const result = await api(`/ai/requests/${encodeURIComponent(requestId)}/result`);
          const receipt = await api(`/ai/requests/${encodeURIComponent(requestId)}/receipt`);
          const synced = await api(`/ai/chat/sessions/${encodeURIComponent(activeSessionId)}/sync?request_id=${encodeURIComponent(requestId)}`, {method: "POST"});
          render(resultOutput, result);
          render(receiptOutput, receipt);
          render(statusOutput, synced);
          if (synced.assistant_message) appendMessage("assistant", synced.assistant_message.content, synced.assistant_message.receipt_hash || "verified");
          return;
        }
        if (status.status === "failed" || status.status === "canceled") return;
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
      setState("timeout");
    }

    document.getElementById("request-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      submitButton.disabled = true;
      setState("creating");
      resultOutput.textContent = "{}";
      receiptOutput.textContent = "{}";
      try {
        const sessionId = await ensureSession();
        const prompt = document.getElementById("prompt").value;
        appendMessage("user", prompt, "queued");
        const created = await api(`/ai/chat/sessions/${encodeURIComponent(sessionId)}/messages`, {
          method: "POST",
          body: JSON.stringify({
            prompt
          })
        });
        render(statusOutput, created);
        await pollRequest(created.ai_request.request_id);
      } catch (error) {
        setState("error");
        render(statusOutput, error);
      } finally {
        submitButton.disabled = false;
      }
    });

    document.getElementById("capabilities-button").addEventListener("click", async () => {
      try {
        render(statusOutput, await api("/ai/capabilities"));
        setState("capabilities");
      } catch (error) {
        setState("error");
        render(statusOutput, error);
      }
    });
  </script>
</body>
</html>"""
