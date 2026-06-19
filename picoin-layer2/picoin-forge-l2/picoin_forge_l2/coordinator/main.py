from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from picoin_forge_l2.common.constants import DEFAULT_COORDINATOR_STATE_DIR, DEFAULT_EPOCH_REWARD_PI
from picoin_forge_l2.common.crypto import request_signing_payload, verify_message
from picoin_forge_l2.common.models import (
    BenchmarkResult,
    ChallengeCreateRequest,
    ChallengeResult,
    Heartbeat,
    WorkloadClaimRequest,
    WorkloadCreateRequest,
    WorkloadResult,
    WorkerRegistration,
)

from .challenge_engine import ChallengeEngine
from .demo import run_demo_network
from .epoch_engine import EpochEngine
from .maintenance import challenge_expiration_loop, expire_challenges_once
from .settlement import build_settlement_payload_preview, list_settlements, read_settlement, summarize_settlement
from .storage import CoordinatorStorage
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
    benchmark_metric_count = len(storage.list_benchmark_metrics(limit=1000))
    challenge_metric_count = len(storage.list_challenge_metrics(limit=1000))
    settlements = list_settlements(DEFAULT_COORDINATOR_STATE_DIR, limit=10)
    rows = "\n".join(
        f"<tr><td>{state.registration.worker_id}</td><td>{state.registration.wallet}</td>"
        f"<td>{state.registration.status.value}</td><td>{state.verified_compute_score:.4f}</td>"
        f"<td>{state.uptime_score:.2f}</td><td>{state.reliability_score:.2f}</td>"
        f"<td>{state.penalty_score:.2f}</td></tr>"
        for state in workers
    )
    event_rows = "\n".join(
        f"<li><code>{event.event_type}</code> {event.subject_id or ''} <small>{event.created_at}</small></li>"
        for event in events
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
  </style>
</head>
<body>
  <h1>Picoin Forge L2 Coordinator</h1>
  <p>
    Workers: <strong>{len(workers)}</strong> |
    Settlements: <strong>{len(settlements)}</strong> |
    Benchmark metrics: <strong>{benchmark_metric_count}</strong> |
    Challenge metrics: <strong>{challenge_metric_count}</strong>
  </p>
  <h2>Workers</h2>
  <table>
    <thead><tr><th>Worker</th><th>Wallet</th><th>Status</th><th>Score</th><th>Uptime</th><th>Reliability</th><th>Penalty</th></tr></thead>
    <tbody>{rows or '<tr><td colspan="7">No workers registered.</td></tr>'}</tbody>
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


@api.get("/events")
def events_api(limit: int = 100) -> list[dict]:
    events = CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR).list_events(limit=limit)
    return [event.model_dump(mode="json") for event in events]


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
    app.add_typer(epoch_app, name="epoch")
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

    @app.command("expire-challenges")
    def expire_challenges(state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR)) -> None:
        console.print_json(data=expire_challenges_once(state_dir))

    @app.command()
    def demo(
        state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR),
        workers: int = 3,
        epoch_reward: float = DEFAULT_EPOCH_REWARD_PI,
    ) -> None:
        result = run_demo_network(state_dir, worker_count=workers, epoch_reward=epoch_reward)
        console.print_json(data=result.model_dump(mode="json"))
else:  # pragma: no cover
    app = None


def bar_width(value: float, max_value: float) -> float:
    if max_value <= 0:
        return 0.0
    return round(min(max(value / max_value, 0.0), 1.0) * 100.0, 2)
