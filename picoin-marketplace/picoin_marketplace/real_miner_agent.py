from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Any

from . import worker_agent
from .models import ExecutionStatus, HardwareType, WorkerStatus, utc_now
from .scanner_worker import env_int
from .worker_agent import WorkerAgentConfig, env_bool, env_float, split_csv


def split_command(value: str | None) -> list[str]:
    if not value or not value.strip():
        return []
    return shlex.split(value, posix=os.name != "nt")


@dataclass(frozen=True)
class RealMinerConfig:
    marketplace_url: str = "http://127.0.0.1:9410"
    interval_seconds: int = 30
    worker_id: str | None = None
    provider_id: str = "provider-1"
    provider_wallet: str = "PI_PROVIDER_WALLET"
    pool_id: str = ""
    hardware_type: HardwareType = HardwareType.CPU
    paired_coin: str = "MONERO"
    title: str | None = "Real PICOIN/MONERO miner"
    units_total: int = 1
    price_pi_per_hour: float = 1.0
    min_booking_minutes: int = 30
    region: str | None = None
    capabilities: list[str] = field(default_factory=lambda: ["randomx", "monero", "real-miner"])
    endpoint_url: str | None = None
    agent_version: str = "0.1.0-real"
    miner_command: list[str] = field(default_factory=list)
    miner_cwd: str | None = None
    miner_api_url: str | None = None
    miner_api_token: str | None = None
    warmup_seconds: int = 5
    restart_on_exit: bool = True
    no_start: bool = False


def config_from_env() -> RealMinerConfig:
    return RealMinerConfig(
        marketplace_url=os.getenv("PICOIN_MARKETPLACE_URL", "http://127.0.0.1:9410").rstrip("/"),
        interval_seconds=max(1, env_int("PICOIN_MARKETPLACE_MINER_INTERVAL_SECONDS", 30)),
        worker_id=os.getenv("PICOIN_MARKETPLACE_WORKER_ID") or None,
        provider_id=os.getenv("PICOIN_MARKETPLACE_WORKER_PROVIDER_ID", "provider-1"),
        provider_wallet=os.getenv("PICOIN_MARKETPLACE_WORKER_PROVIDER_WALLET", "PI_PROVIDER_WALLET"),
        pool_id=os.getenv("PICOIN_MARKETPLACE_WORKER_POOL_ID", ""),
        hardware_type=HardwareType(os.getenv("PICOIN_MARKETPLACE_WORKER_HARDWARE_TYPE", "cpu").lower()),
        paired_coin=os.getenv("PICOIN_MARKETPLACE_MINER_PAIRED_COIN", "MONERO").upper(),
        title=os.getenv("PICOIN_MARKETPLACE_WORKER_TITLE", "Real PICOIN/MONERO miner") or None,
        units_total=max(1, env_int("PICOIN_MARKETPLACE_WORKER_UNITS_TOTAL", 1)),
        price_pi_per_hour=env_float("PICOIN_MARKETPLACE_WORKER_PRICE_PI_PER_HOUR", 1.0),
        min_booking_minutes=max(1, env_int("PICOIN_MARKETPLACE_WORKER_MIN_BOOKING_MINUTES", 30)),
        region=os.getenv("PICOIN_MARKETPLACE_WORKER_REGION") or None,
        capabilities=split_csv(os.getenv("PICOIN_MARKETPLACE_WORKER_CAPABILITIES"))
        or ["randomx", "monero", "real-miner"],
        endpoint_url=os.getenv("PICOIN_MARKETPLACE_WORKER_ENDPOINT_URL") or None,
        agent_version=os.getenv("PICOIN_MARKETPLACE_WORKER_AGENT_VERSION", "0.1.0-real"),
        miner_command=split_command(os.getenv("PICOIN_MARKETPLACE_MINER_COMMAND")),
        miner_cwd=os.getenv("PICOIN_MARKETPLACE_MINER_CWD") or None,
        miner_api_url=os.getenv("PICOIN_MARKETPLACE_MINER_API_URL") or None,
        miner_api_token=os.getenv("PICOIN_MARKETPLACE_MINER_API_TOKEN") or None,
        warmup_seconds=max(0, env_int("PICOIN_MARKETPLACE_MINER_WARMUP_SECONDS", 5)),
        restart_on_exit=env_bool("PICOIN_MARKETPLACE_MINER_RESTART_ON_EXIT", True),
        no_start=env_bool("PICOIN_MARKETPLACE_MINER_NO_START", False),
    )


def discover_pool_id(config: RealMinerConfig) -> str:
    if config.pool_id:
        return config.pool_id
    query = (
        f"{config.marketplace_url}/pools"
        f"?hardware_type={config.hardware_type.value}"
        f"&paired_coin={config.paired_coin}"
        "&active_only=true&limit=100"
    )
    pools = worker_agent.json_get(query)
    if not isinstance(pools, list):
        raise ValueError("marketplace pools response is not a list")
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        if str(pool.get("pair_symbol", "")).upper() == f"PICOIN/{config.paired_coin}":
            return str(pool["pool_id"])
    raise ValueError(f"active PICOIN/{config.paired_coin} {config.hardware_type.value} pool not found")


def start_miner_process(config: RealMinerConfig) -> subprocess.Popen[bytes] | None:
    if config.no_start:
        return None
    if not config.miner_command:
        raise ValueError("miner command is required; set PICOIN_MARKETPLACE_MINER_COMMAND or pass --command")
    return subprocess.Popen(
        config.miner_command,
        cwd=config.miner_cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def read_miner_api(config: RealMinerConfig) -> dict[str, Any]:
    if not config.miner_api_url:
        return {}
    headers = {}
    if config.miner_api_token:
        headers["Authorization"] = f"Bearer {config.miner_api_token}"
    request = urllib.request.Request(config.miner_api_url, headers=headers)
    timeout = float(os.getenv("PICOIN_MARKETPLACE_HTTP_TIMEOUT_SECONDS", "30"))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("miner API response is not an object")
    return payload


def parse_xmrig_summary(summary: dict[str, Any]) -> dict[str, Any]:
    hashrates = (summary.get("hashrate") or {}).get("total") or []
    hashrate = next((item for item in hashrates if isinstance(item, int | float)), None)
    results = summary.get("results") or {}
    accepted = results.get("shares_good")
    total = results.get("shares_total")
    rejected = total - accepted if isinstance(total, int) and isinstance(accepted, int) and total >= accepted else None
    uptime = summary.get("uptime") or (summary.get("connection") or {}).get("uptime")
    return {
        "reported_hashrate": float(hashrate) if hashrate is not None else None,
        "accepted_shares": accepted if isinstance(accepted, int) else None,
        "rejected_shares": rejected,
        "uptime_seconds": uptime if isinstance(uptime, int) else None,
        "api": {
            "kind": "xmrig",
            "worker_id": summary.get("worker_id"),
            "algo": summary.get("algo"),
            "connection": summary.get("connection"),
        },
    }


def miner_runtime_state(config: RealMinerConfig, process: subprocess.Popen[bytes] | None) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "real_miner": True,
        "paired_coin": config.paired_coin,
        "pair_symbol": f"PICOIN/{config.paired_coin}",
        "hardware_type": config.hardware_type.value,
        "miner_started_by_agent": process is not None,
    }
    if config.miner_command:
        metrics["miner_binary"] = config.miner_command[0]
        metrics["miner_arg_count"] = max(0, len(config.miner_command) - 1)
    if process is not None:
        metrics["miner_pid"] = process.pid
        metrics["miner_exit_code"] = process.poll()
    if config.miner_api_url:
        metrics["miner_api_url"] = config.miner_api_url

    parsed: dict[str, Any] = {
        "reported_hashrate": None,
        "accepted_shares": None,
        "rejected_shares": None,
        "uptime_seconds": None,
    }
    try:
        summary = read_miner_api(config)
        if summary:
            parsed = parse_xmrig_summary(summary)
            metrics["miner_api"] = parsed.pop("api")
    except Exception as exc:
        metrics["miner_api_error"] = str(exc)

    running = process is None or process.poll() is None
    if config.miner_api_url and metrics.get("miner_api_error"):
        running = process is not None and process.poll() is None
    metrics["miner_running"] = running
    return {"metrics": metrics, "running": running, **parsed}


def worker_config_for_miner(
    config: RealMinerConfig,
    *,
    pool_id: str,
    process: subprocess.Popen[bytes] | None,
) -> WorkerAgentConfig:
    state = miner_runtime_state(config, process)
    status = WorkerStatus.ONLINE if state["running"] else WorkerStatus.OFFLINE
    return WorkerAgentConfig(
        marketplace_url=config.marketplace_url,
        interval_seconds=config.interval_seconds,
        worker_id=config.worker_id,
        provider_id=config.provider_id,
        provider_wallet=config.provider_wallet,
        pool_id=pool_id,
        hardware_type=config.hardware_type,
        title=config.title,
        units_total=config.units_total,
        units_available=config.units_total if status == WorkerStatus.ONLINE else 0,
        price_pi_per_hour=config.price_pi_per_hour,
        min_booking_minutes=config.min_booking_minutes,
        region=config.region,
        capabilities=config.capabilities,
        endpoint_url=config.endpoint_url,
        agent_version=config.agent_version,
        status=status,
        metrics=state["metrics"],
        report_assignments=True,
        report_status=ExecutionStatus.RUNNING if status == WorkerStatus.ONLINE else ExecutionStatus.FAILED,
        reported_hashrate=state["reported_hashrate"],
        accepted_shares=state["accepted_shares"],
        rejected_shares=state["rejected_shares"],
        uptime_seconds=state["uptime_seconds"],
    )


def stop_miner_process(process: subprocess.Popen[bytes] | None, *, timeout_seconds: float = 10.0) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout_seconds)


def run_miner_once(
    config: RealMinerConfig,
    *,
    register: bool = True,
    worker_id: str | None = None,
    process: subprocess.Popen[bytes] | None = None,
    stop_after: bool = True,
) -> dict[str, Any]:
    pool_id = discover_pool_id(config)
    started_process = False
    if process is None:
        process = start_miner_process(config)
        started_process = process is not None
        if started_process and config.warmup_seconds:
            time.sleep(config.warmup_seconds)
    try:
        agent_config = worker_config_for_miner(config, pool_id=pool_id, process=process)
        result = worker_agent.run_once(agent_config, register=register, worker_id=worker_id)
        result["pool_id"] = pool_id
        result["miner"] = agent_config.metrics
        return result
    finally:
        if stop_after and started_process:
            stop_miner_process(process)


def run_forever(config: RealMinerConfig, *, register: bool = True) -> None:
    pool_id = discover_pool_id(config)
    process = start_miner_process(config)
    worker_id = config.worker_id
    if process is not None and config.warmup_seconds:
        time.sleep(config.warmup_seconds)
    try:
        while True:
            if process is not None and process.poll() is not None and config.restart_on_exit:
                process = start_miner_process(config)
                if process is not None and config.warmup_seconds:
                    time.sleep(config.warmup_seconds)
            try:
                agent_config = worker_config_for_miner(config, pool_id=pool_id, process=process)
                result = worker_agent.run_once(
                    agent_config,
                    register=register or not worker_id,
                    worker_id=worker_id,
                )
                worker_id = str(result["worker_id"])
                register = False
                result["pool_id"] = pool_id
                result["miner"] = agent_config.metrics
                print(json.dumps(result, sort_keys=True), flush=True)
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "service": "picoin-marketplace-miner",
                            "error": str(exc),
                            "checked_at": utc_now().isoformat(),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            time.sleep(config.interval_seconds)
    finally:
        stop_miner_process(process)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a real external miner and report it as PICOIN paired marketplace capacity."
    )
    parser.add_argument("--once", action="store_true", help="start miner, send one heartbeat/report, then stop")
    parser.add_argument("--no-register", action="store_true", help="only send heartbeat to an existing worker")
    parser.add_argument("--no-start", action="store_true", default=None, help="do not launch a process; monitor an existing miner")
    parser.add_argument("--marketplace-url", default=None)
    parser.add_argument("--interval-seconds", type=int, default=None)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--provider-id", default=None)
    parser.add_argument("--provider-wallet", default=None)
    parser.add_argument("--pool-id", default=None)
    parser.add_argument("--hardware-type", choices=[item.value for item in HardwareType], default=None)
    parser.add_argument("--paired-coin", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--units-total", type=int, default=None)
    parser.add_argument("--price-pi-per-hour", type=float, default=None)
    parser.add_argument("--region", default=None)
    parser.add_argument("--capability", action="append", default=None)
    parser.add_argument("--command", default=None, help="external miner command, for example xmrig with pool args")
    parser.add_argument("--miner-cwd", default=None)
    parser.add_argument("--xmrig-api-url", default=None)
    parser.add_argument("--xmrig-api-token", default=None)
    parser.add_argument("--warmup-seconds", type=int, default=None)
    return parser


def config_from_args(args: argparse.Namespace) -> RealMinerConfig:
    base = config_from_env()
    return replace(
        base,
        marketplace_url=(args.marketplace_url or base.marketplace_url).rstrip("/"),
        interval_seconds=max(1, args.interval_seconds or base.interval_seconds),
        worker_id=args.worker_id or base.worker_id,
        provider_id=args.provider_id or base.provider_id,
        provider_wallet=args.provider_wallet or base.provider_wallet,
        pool_id=args.pool_id or base.pool_id,
        hardware_type=HardwareType(args.hardware_type or base.hardware_type.value),
        paired_coin=(args.paired_coin or base.paired_coin).upper(),
        title=args.title or base.title,
        units_total=max(1, args.units_total or base.units_total),
        price_pi_per_hour=args.price_pi_per_hour or base.price_pi_per_hour,
        region=args.region or base.region,
        capabilities=args.capability or base.capabilities,
        miner_command=split_command(args.command) or base.miner_command,
        miner_cwd=args.miner_cwd or base.miner_cwd,
        miner_api_url=args.xmrig_api_url or base.miner_api_url,
        miner_api_token=args.xmrig_api_token or base.miner_api_token,
        warmup_seconds=max(0, args.warmup_seconds if args.warmup_seconds is not None else base.warmup_seconds),
        no_start=base.no_start if args.no_start is None else args.no_start,
    )


def main() -> None:
    args = build_parser().parse_args()
    config = config_from_args(args)
    register = not args.no_register
    if args.once:
        print(json.dumps(run_miner_once(config, register=register), indent=2, sort_keys=True))
        return
    run_forever(config, register=register)


if __name__ == "__main__":
    main()
