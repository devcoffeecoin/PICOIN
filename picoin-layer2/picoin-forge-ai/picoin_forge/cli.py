from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .analyzer import explain_repo
from .fixer import generate_fix_suggestions
from .health import generate_health_assets
from .installer import generate_install_plan
from .l2_jobs import create_l2_job, verify_l2_job
from .scanner import scan_repo
from .service_generator import generate_systemd_services

app = typer.Typer(help="Picoin Forge AI CLI.")
l2_app = typer.Typer(help="Simulated Picoin Forge AI Layer 2 jobs.")
app.add_typer(l2_app, name="l2-job")
console = Console()


@app.command()
def scan(repo_path: Path) -> None:
    """Scan a repository and generate .picoin-forge/project-map.json."""
    project_map = scan_repo(repo_path)
    console.print_json(data=project_map)


@app.command()
def explain(repo_path: Path) -> None:
    """Explain the detected repository architecture."""
    console.print(explain_repo(repo_path))


@app.command()
def install(repo_path: Path) -> None:
    """Generate a Linux install script without executing it."""
    path = generate_install_plan(repo_path)
    console.print(f"[green]Generated install script:[/green] {path}")


@app.command()
def service(repo_path: Path) -> None:
    """Generate systemd unit files for detected services."""
    paths = generate_systemd_services(repo_path)
    for path in paths:
        console.print(f"[green]Generated systemd service:[/green] {path}")


@app.command()
def health(repo_path: Path) -> None:
    """Generate static health report and health check script."""
    paths = generate_health_assets(repo_path)
    console.print(f"[green]Generated health script:[/green] {paths['script']}")
    console.print(f"[green]Generated health report:[/green] {paths['report']}")


@app.command()
def fix(repo_path: Path) -> None:
    """Analyze logs and generate fix suggestions without modifying code."""
    path = generate_fix_suggestions(repo_path)
    console.print(f"[green]Generated fix suggestions:[/green] {path}")


@l2_app.command("create")
def l2_create(
    repo_path: Path = typer.Option(Path("."), "--repo-path", help="Local repository path."),
    job_type: str = typer.Option("setup_node", "--job-type"),
    repo_url: str = typer.Option("", "--repo-url"),
    reward: str = typer.Option("50 PI", "--reward"),
    requirement: Optional[list[str]] = typer.Option(None, "--requirement"),
) -> None:
    """Create a simulated Layer 2 job JSON model."""
    job = create_l2_job(repo_path, job_type=job_type, repo_url=repo_url, reward=reward, requirements=requirement)
    console.print_json(data=job)


@l2_app.command("verify")
def l2_verify(
    repo_path: Path = typer.Option(Path("."), "--repo-path", help="Local repository path."),
    job_id: Optional[str] = typer.Option(None, "--job-id"),
) -> None:
    """Verify simulated job evidence and generate a result_hash."""
    job = verify_l2_job(repo_path, job_id=job_id)
    console.print_json(data=job)


if __name__ == "__main__":
    app()
