from __future__ import annotations

import json
from pathlib import Path

from .benchmark import run_benchmark
from .client import CoordinatorClient
from .config import load_worker_config, save_worker_config, worker_state_dir
from .heartbeat import build_heartbeat
from .loop import run_worker_loop, run_worker_once
from .registration import load_private_key, load_registration, register_worker, rotate_worker_key

try:  # pragma: no cover - exercised when optional CLI deps are installed.
    import typer
    from rich.console import Console
except ModuleNotFoundError:  # pragma: no cover
    typer = None
    Console = None


if typer is not None:
    app = typer.Typer(help="Picoin Forge L2 worker CLI.")
    console = Console()

    @app.command()
    def register(
        wallet: str = typer.Option(..., "--wallet", help="PI wallet address for this worker."),
        state_dir: Path = Path(".picoin-forge-worker"),
        coordinator_url: str = "http://127.0.0.1:9380",
    ) -> None:
        save_worker_config(state_dir, wallet=wallet, coordinator_url=coordinator_url)
        registration = register_worker(wallet, worker_state_dir(state_dir))
        console.print_json(data=registration.model_dump(mode="json"))

    @app.command()
    def configure(
        wallet: str = typer.Option(..., "--wallet", help="PI wallet address for this worker."),
        coordinator_url: str = "http://127.0.0.1:9380",
        state_dir: Path = Path(".picoin-forge-worker"),
        interval_seconds: float = 30.0,
        benchmark_scale: int = 1,
    ) -> None:
        config = save_worker_config(
            state_dir,
            wallet=wallet,
            coordinator_url=coordinator_url,
            interval_seconds=interval_seconds,
            benchmark_scale=benchmark_scale,
        )
        console.print_json(data=config.model_dump(mode="json"))

    @app.command()
    def benchmark(state_dir: Path = Path(".picoin-forge-worker"), scale: int = 1) -> None:
        registration = load_registration(worker_state_dir(state_dir))
        result = run_benchmark(registration.worker_id, scale=scale)
        path = worker_state_dir(state_dir) / "benchmark.json"
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        console.print_json(data=result.model_dump(mode="json"))

    @app.command()
    def start(state_dir: Path = Path(".picoin-forge-worker")) -> None:
        registration = load_registration(worker_state_dir(state_dir))
        heartbeat = build_heartbeat(registration.worker_id)
        path = worker_state_dir(state_dir) / "heartbeat.json"
        path.write_text(heartbeat.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"[green]Worker heartbeat generated:[/green] {path}")

    @app.command("rotate-key")
    def rotate_key(
        state_dir: Path = Path(".picoin-forge-worker"),
        coordinator_url: str | None = None,
        submit: bool = False,
    ) -> None:
        state_path = worker_state_dir(state_dir)
        registration = rotate_worker_key(state_path)
        response = None
        if submit:
            config = load_worker_config(state_path, required=False)
            target_url = (coordinator_url or (config.coordinator_url if config else None) or "http://127.0.0.1:9380").rstrip("/")
            response = CoordinatorClient(
                target_url,
                private_key=load_private_key(state_path),
                worker_id=registration.worker_id,
            ).register(registration)
        payload = registration.model_dump(mode="json")
        payload["submitted"] = response is not None
        if response is not None:
            payload["coordinator_response"] = response
        console.print_json(data=payload)

    @app.command("loop-once")
    def loop_once(
        coordinator_url: str | None = None,
        state_dir: Path = Path(".picoin-forge-worker"),
    ) -> None:
        result = run_worker_once(state_dir=state_dir, coordinator_url=coordinator_url)
        console.print_json(data=result)

    @app.command("loop")
    def loop(
        coordinator_url: str | None = None,
        state_dir: Path = Path(".picoin-forge-worker"),
        interval_seconds: float | None = None,
        iterations: int | None = None,
    ) -> None:
        result = run_worker_loop(
            state_dir=state_dir,
            coordinator_url=coordinator_url,
            interval_seconds=interval_seconds,
            iterations=iterations,
        )
        console.print_json(data=result)

    @app.command()
    def status(state_dir: Path = Path(".picoin-forge-worker")) -> None:
        state_path = worker_state_dir(state_dir)
        payload = {
            "registered": (state_path / "worker.json").exists(),
            "configured": (state_path / "config.json").exists(),
            "benchmark": (state_path / "benchmark.json").exists(),
            "heartbeat": (state_path / "heartbeat.json").exists(),
            "state_dir": str(state_path),
        }
        config = load_worker_config(state_path, required=False)
        if config:
            payload["coordinator_url"] = config.coordinator_url
            payload["wallet"] = config.wallet
        console.print_json(data=payload)
else:  # pragma: no cover
    app = None


def read_worker_status(state_dir: str | Path) -> dict:
    path = worker_state_dir(state_dir)
    return {
        "registered": (path / "worker.json").exists(),
        "configured": (path / "config.json").exists(),
        "benchmark": (path / "benchmark.json").exists(),
        "heartbeat": (path / "heartbeat.json").exists(),
        "state_dir": str(path),
    }
