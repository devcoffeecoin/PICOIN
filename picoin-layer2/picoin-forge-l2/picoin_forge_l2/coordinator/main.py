from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from picoin_forge_l2.common.constants import DEFAULT_COORDINATOR_STATE_DIR, DEFAULT_EPOCH_REWARD_PI
from picoin_forge_l2.common.models import (
    BenchmarkResult,
    ChallengeCreateRequest,
    ChallengeResult,
    Heartbeat,
    WorkerRegistration,
)

from .challenge_engine import ChallengeEngine
from .demo import run_demo_network
from .epoch_engine import EpochEngine
from .storage import CoordinatorStorage
from .worker_registry import WorkerRegistry

try:  # pragma: no cover - exercised when optional CLI deps are installed.
    import typer
    from rich.console import Console
except ModuleNotFoundError:  # pragma: no cover
    typer = None
    Console = None

api = FastAPI(title="Picoin Forge L2 Coordinator", version="0.1.0")


def registry(state_dir: str | Path | None = None) -> WorkerRegistry:
    return WorkerRegistry(state_dir or DEFAULT_COORDINATOR_STATE_DIR)


def challenges(state_dir: str | Path | None = None) -> ChallengeEngine:
    reg = registry(state_dir)
    return ChallengeEngine(state_dir or DEFAULT_COORDINATOR_STATE_DIR, reg)


@api.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "picoin-forge-l2-coordinator"}


@api.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    workers = registry().all()
    events = CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR).list_events(limit=20)
    settlements = sorted(Path(DEFAULT_COORDINATOR_STATE_DIR).glob("settlement_epoch_*.json"))
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
  </style>
</head>
<body>
  <h1>Picoin Forge L2 Coordinator</h1>
  <p>Workers: <strong>{len(workers)}</strong> | Settlements: <strong>{len(settlements)}</strong></p>
  <h2>Workers</h2>
  <table>
    <thead><tr><th>Worker</th><th>Wallet</th><th>Status</th><th>Score</th><th>Uptime</th><th>Reliability</th><th>Penalty</th></tr></thead>
    <tbody>{rows or '<tr><td colspan="7">No workers registered.</td></tr>'}</tbody>
  </table>
  <h2>Recent Events</h2>
  <ul>{event_rows or '<li>No events yet.</li>'}</ul>
</body>
</html>"""


@api.get("/events")
def events_api(limit: int = 100) -> list[dict]:
    events = CoordinatorStorage(DEFAULT_COORDINATOR_STATE_DIR).list_events(limit=limit)
    return [event.model_dump(mode="json") for event in events]


@api.post("/workers/register")
def register_worker_api(registration: WorkerRegistration) -> dict:
    state = registry().register(registration)
    return state.model_dump(mode="json")


@api.get("/workers")
def workers_api() -> list[dict]:
    return [state.model_dump(mode="json") for state in registry().all()]


@api.post("/benchmarks")
def benchmark_api(benchmark: BenchmarkResult) -> dict:
    state = registry().update_benchmark(benchmark)
    return state.model_dump(mode="json")


@api.post("/heartbeats")
def heartbeat_api(heartbeat: Heartbeat) -> dict:
    state = registry().heartbeat(heartbeat)
    return state.model_dump(mode="json")


@api.post("/challenges")
def create_challenge_api(payload: ChallengeCreateRequest) -> dict:
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
def submit_challenge_api(challenge_id: str, result: ChallengeResult) -> dict:
    if result.challenge_id != challenge_id:
        return {"accepted": False, "reason": "challenge_id mismatch"}
    passed = challenges().verify_result(result)
    return {"accepted": passed, "challenge_id": challenge_id}


@api.post("/challenges/expire")
def expire_challenges_api() -> dict:
    expired = challenges().expire_open_challenges()
    return {
        "expired": len(expired),
        "challenges": [challenge.model_dump(mode="json") for challenge in expired],
    }


@api.post("/epochs/close")
def close_epoch_api(epoch_reward: float = DEFAULT_EPOCH_REWARD_PI) -> dict:
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

    @app.command()
    def events(state_dir: Path = Path(DEFAULT_COORDINATOR_STATE_DIR), limit: int = 20) -> None:
        rows = [event.model_dump(mode="json") for event in CoordinatorStorage(state_dir).list_events(limit=limit)]
        console.print_json(data=rows)

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
