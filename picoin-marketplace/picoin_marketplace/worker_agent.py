from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .models import ExecutionStatus, HardwareType, WorkerStatus, utc_now
from .scanner_worker import env_int


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class WorkerAgentConfig:
    marketplace_url: str = "http://127.0.0.1:9410"
    interval_seconds: int = 30
    worker_id: str | None = None
    provider_id: str = "provider-1"
    provider_wallet: str = "PI_PROVIDER_WALLET"
    pool_id: str = ""
    hardware_type: HardwareType = HardwareType.GPU
    title: str | None = None
    units_total: int = 1
    units_available: int | None = None
    price_pi_per_hour: float = 1.0
    min_booking_minutes: int = 30
    region: str | None = None
    capabilities: list[str] = field(default_factory=list)
    endpoint_url: str | None = None
    agent_version: str = "0.1.0"
    status: WorkerStatus = WorkerStatus.ONLINE
    metrics: dict[str, Any] = field(default_factory=dict)
    report_assignments: bool = False
    report_status: ExecutionStatus = ExecutionStatus.RUNNING
    reported_hashrate: float | None = None
    accepted_shares: int | None = None
    rejected_shares: int | None = None
    uptime_seconds: int | None = None


def config_from_env() -> WorkerAgentConfig:
    return WorkerAgentConfig(
        marketplace_url=os.getenv("PICOIN_MARKETPLACE_URL", "http://127.0.0.1:9410").rstrip("/"),
        interval_seconds=max(1, env_int("PICOIN_MARKETPLACE_WORKER_INTERVAL_SECONDS", 30)),
        worker_id=os.getenv("PICOIN_MARKETPLACE_WORKER_ID") or None,
        provider_id=os.getenv("PICOIN_MARKETPLACE_WORKER_PROVIDER_ID", "provider-1"),
        provider_wallet=os.getenv("PICOIN_MARKETPLACE_WORKER_PROVIDER_WALLET", "PI_PROVIDER_WALLET"),
        pool_id=os.getenv("PICOIN_MARKETPLACE_WORKER_POOL_ID", ""),
        hardware_type=HardwareType(os.getenv("PICOIN_MARKETPLACE_WORKER_HARDWARE_TYPE", "gpu").lower()),
        title=os.getenv("PICOIN_MARKETPLACE_WORKER_TITLE") or None,
        units_total=max(1, env_int("PICOIN_MARKETPLACE_WORKER_UNITS_TOTAL", 1)),
        units_available=env_int("PICOIN_MARKETPLACE_WORKER_UNITS_AVAILABLE", 0)
        if os.getenv("PICOIN_MARKETPLACE_WORKER_UNITS_AVAILABLE")
        else None,
        price_pi_per_hour=env_float("PICOIN_MARKETPLACE_WORKER_PRICE_PI_PER_HOUR", 1.0),
        min_booking_minutes=max(1, env_int("PICOIN_MARKETPLACE_WORKER_MIN_BOOKING_MINUTES", 30)),
        region=os.getenv("PICOIN_MARKETPLACE_WORKER_REGION") or None,
        capabilities=split_csv(os.getenv("PICOIN_MARKETPLACE_WORKER_CAPABILITIES")),
        endpoint_url=os.getenv("PICOIN_MARKETPLACE_WORKER_ENDPOINT_URL") or None,
        agent_version=os.getenv("PICOIN_MARKETPLACE_WORKER_AGENT_VERSION", "0.1.0"),
        status=WorkerStatus(os.getenv("PICOIN_MARKETPLACE_WORKER_STATUS", "online").lower()),
        report_assignments=env_bool("PICOIN_MARKETPLACE_WORKER_REPORT_ASSIGNMENTS", False),
        report_status=ExecutionStatus(os.getenv("PICOIN_MARKETPLACE_WORKER_REPORT_STATUS", "running").lower()),
        reported_hashrate=env_float("PICOIN_MARKETPLACE_WORKER_HASHRATE", 0.0)
        if os.getenv("PICOIN_MARKETPLACE_WORKER_HASHRATE")
        else None,
        accepted_shares=env_int("PICOIN_MARKETPLACE_WORKER_ACCEPTED_SHARES", 0)
        if os.getenv("PICOIN_MARKETPLACE_WORKER_ACCEPTED_SHARES")
        else None,
        rejected_shares=env_int("PICOIN_MARKETPLACE_WORKER_REJECTED_SHARES", 0)
        if os.getenv("PICOIN_MARKETPLACE_WORKER_REJECTED_SHARES")
        else None,
        uptime_seconds=env_int("PICOIN_MARKETPLACE_WORKER_UPTIME_SECONDS", 0)
        if os.getenv("PICOIN_MARKETPLACE_WORKER_UPTIME_SECONDS")
        else None,
    )


def register_payload(config: WorkerAgentConfig) -> dict[str, Any]:
    payload = {
        "provider_id": config.provider_id,
        "provider_wallet": config.provider_wallet,
        "pool_id": config.pool_id,
        "hardware_type": config.hardware_type.value,
        "units_total": config.units_total,
        "price_pi_per_hour": config.price_pi_per_hour,
        "min_booking_minutes": config.min_booking_minutes,
        "capabilities": config.capabilities,
        "endpoint_url": config.endpoint_url,
        "agent_version": config.agent_version,
    }
    optional = {
        "worker_id": config.worker_id,
        "title": config.title,
        "region": config.region,
    }
    for key, value in optional.items():
        if value:
            payload[key] = value
    return payload


def heartbeat_payload(config: WorkerAgentConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": config.status.value,
        "units_total": config.units_total,
        "metrics": config.metrics,
        "agent_version": config.agent_version,
    }
    if config.units_available is not None:
        payload["units_available"] = config.units_available
    if config.endpoint_url:
        payload["endpoint_url"] = config.endpoint_url
    return payload


def json_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    timeout = float(os.getenv("PICOIN_MARKETPLACE_HTTP_TIMEOUT_SECONDS", "30"))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("marketplace response is not an object")
    return data


def json_get(url: str) -> Any:
    timeout = float(os.getenv("PICOIN_MARKETPLACE_HTTP_TIMEOUT_SECONDS", "30"))
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def register_worker(config: WorkerAgentConfig) -> dict[str, Any]:
    if not config.pool_id:
        raise ValueError("pool_id is required")
    return json_post(f"{config.marketplace_url}/workers/register", register_payload(config))


def heartbeat_worker(config: WorkerAgentConfig, worker_id: str) -> dict[str, Any]:
    return json_post(f"{config.marketplace_url}/workers/{worker_id}/heartbeat", heartbeat_payload(config))


def fetch_assignments(config: WorkerAgentConfig, worker_id: str) -> list[dict[str, Any]]:
    data = json_get(f"{config.marketplace_url}/workers/{worker_id}/assignments?active_only=true&limit=100")
    if not isinstance(data, list):
        raise ValueError("assignments response is not a list")
    return [item for item in data if isinstance(item, dict)]


def assignment_report_payload(config: WorkerAgentConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": config.report_status.value,
        "metrics": config.metrics,
    }
    optional = {
        "reported_hashrate": config.reported_hashrate,
        "accepted_shares": config.accepted_shares,
        "rejected_shares": config.rejected_shares,
        "uptime_seconds": config.uptime_seconds,
    }
    for key, value in optional.items():
        if value is not None:
            payload[key] = value
    return payload


def report_assignment(config: WorkerAgentConfig, worker_id: str, assignment: dict[str, Any]) -> dict[str, Any]:
    booking_id = str(assignment.get("booking_id") or "").strip()
    if not booking_id:
        raise ValueError("assignment booking_id is required")
    return json_post(
        f"{config.marketplace_url}/workers/{worker_id}/assignments/{booking_id}/reports",
        assignment_report_payload(config),
    )


def run_once(config: WorkerAgentConfig, *, register: bool = True, worker_id: str | None = None) -> dict[str, Any]:
    started_at = utc_now()
    registered: dict[str, Any] | None = None
    active_worker_id = worker_id or config.worker_id
    if register:
        registered = register_worker(config)
        active_worker_id = str((registered.get("worker") or {}).get("worker_id") or active_worker_id or "")
    if not active_worker_id:
        raise ValueError("worker_id is required when registration is disabled")
    heartbeat = heartbeat_worker(config, active_worker_id)
    assignments = fetch_assignments(config, active_worker_id)
    reports: list[dict[str, Any]] = []
    if config.report_assignments:
        reports = [report_assignment(config, active_worker_id, assignment) for assignment in assignments]
    return {
        "service": "picoin-marketplace-worker",
        "started_at": started_at.isoformat(),
        "finished_at": utc_now().isoformat(),
        "worker_id": active_worker_id,
        "registered": registered,
        "heartbeat": heartbeat,
        "assignments": assignments,
        "assignment_count": len(assignments),
        "reports": reports,
        "report_count": len(reports),
    }


def run_forever(config: WorkerAgentConfig, *, register: bool = True) -> None:
    worker_id = config.worker_id
    while True:
        try:
            result = run_once(config, register=register or not worker_id, worker_id=worker_id)
            worker_id = str(result["worker_id"])
            register = False
            print(json.dumps(result, sort_keys=True), flush=True)
        except Exception as exc:  # pragma: no cover - exercised in service logs
            print(
                json.dumps(
                    {
                        "service": "picoin-marketplace-worker",
                        "error": str(exc),
                        "checked_at": utc_now().isoformat(),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        time.sleep(config.interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Picoin Marketplace provider worker agent.")
    parser.add_argument("--once", action="store_true", help="register and send one heartbeat, then exit")
    parser.add_argument("--no-register", action="store_true", help="only send heartbeat to an existing worker")
    parser.add_argument("--marketplace-url", default=None)
    parser.add_argument("--interval-seconds", type=int, default=None)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--provider-id", default=None)
    parser.add_argument("--provider-wallet", default=None)
    parser.add_argument("--pool-id", default=None)
    parser.add_argument("--hardware-type", choices=[item.value for item in HardwareType], default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--units-total", type=int, default=None)
    parser.add_argument("--units-available", type=int, default=None)
    parser.add_argument("--price-pi-per-hour", type=float, default=None)
    parser.add_argument("--region", default=None)
    parser.add_argument("--capability", action="append", default=None)
    parser.add_argument("--status", choices=[item.value for item in WorkerStatus], default=None)
    parser.add_argument("--report-assignments", action="store_true", default=None)
    parser.add_argument("--report-status", choices=[item.value for item in ExecutionStatus], default=None)
    parser.add_argument("--reported-hashrate", type=float, default=None)
    parser.add_argument("--accepted-shares", type=int, default=None)
    parser.add_argument("--rejected-shares", type=int, default=None)
    parser.add_argument("--uptime-seconds", type=int, default=None)
    return parser


def config_from_args(args: argparse.Namespace) -> WorkerAgentConfig:
    base = config_from_env()
    return WorkerAgentConfig(
        marketplace_url=(args.marketplace_url or base.marketplace_url).rstrip("/"),
        interval_seconds=max(1, args.interval_seconds or base.interval_seconds),
        worker_id=args.worker_id or base.worker_id,
        provider_id=args.provider_id or base.provider_id,
        provider_wallet=args.provider_wallet or base.provider_wallet,
        pool_id=args.pool_id or base.pool_id,
        hardware_type=HardwareType(args.hardware_type or base.hardware_type.value),
        title=args.title or base.title,
        units_total=max(1, args.units_total or base.units_total),
        units_available=args.units_available if args.units_available is not None else base.units_available,
        price_pi_per_hour=args.price_pi_per_hour or base.price_pi_per_hour,
        min_booking_minutes=base.min_booking_minutes,
        region=args.region or base.region,
        capabilities=args.capability or base.capabilities,
        endpoint_url=base.endpoint_url,
        agent_version=base.agent_version,
        status=WorkerStatus(args.status or base.status.value),
        metrics=base.metrics,
        report_assignments=base.report_assignments if args.report_assignments is None else args.report_assignments,
        report_status=ExecutionStatus(args.report_status or base.report_status.value),
        reported_hashrate=args.reported_hashrate if args.reported_hashrate is not None else base.reported_hashrate,
        accepted_shares=args.accepted_shares if args.accepted_shares is not None else base.accepted_shares,
        rejected_shares=args.rejected_shares if args.rejected_shares is not None else base.rejected_shares,
        uptime_seconds=args.uptime_seconds if args.uptime_seconds is not None else base.uptime_seconds,
    )


def main() -> None:
    args = build_parser().parse_args()
    config = config_from_args(args)
    register = not args.no_register
    if args.once:
        print(json.dumps(run_once(config, register=register), indent=2, sort_keys=True))
        return
    run_forever(config, register=register)


if __name__ == "__main__":
    main()
