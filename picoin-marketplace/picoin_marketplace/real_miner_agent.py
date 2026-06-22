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
    picoin_command: list[str] = field(default_factory=list)
    picoin_cwd: str | None = None
    picoin_api_url: str | None = None
    picoin_api_token: str | None = None
    paired_command: list[str] = field(default_factory=list)
    paired_cwd: str | None = None
    paired_api_url: str | None = None
    paired_api_token: str | None = None
    require_picoin_miner: bool = False
    picoin_capacity_percent: int = 10
    paired_capacity_percent: int = 90
    # Legacy single-miner fields. These remain supported as aliases for the
    # paired side so existing XMRig deployments keep working after upgrade.
    miner_command: list[str] = field(default_factory=list)
    miner_cwd: str | None = None
    miner_api_url: str | None = None
    miner_api_token: str | None = None
    warmup_seconds: int = 5
    restart_on_exit: bool = True
    auto_create_pool: bool = True
    no_start: bool = False


def config_from_env() -> RealMinerConfig:
    legacy_command = split_command(os.getenv("PICOIN_MARKETPLACE_MINER_COMMAND"))
    legacy_cwd = os.getenv("PICOIN_MARKETPLACE_MINER_CWD") or None
    legacy_api_url = os.getenv("PICOIN_MARKETPLACE_MINER_API_URL") or None
    legacy_api_token = os.getenv("PICOIN_MARKETPLACE_MINER_API_TOKEN") or None
    paired_command = split_command(os.getenv("PICOIN_MARKETPLACE_PAIRED_MINER_COMMAND")) or legacy_command
    paired_cwd = os.getenv("PICOIN_MARKETPLACE_PAIRED_MINER_CWD") or legacy_cwd
    paired_api_url = os.getenv("PICOIN_MARKETPLACE_PAIRED_MINER_API_URL") or legacy_api_url
    paired_api_token = os.getenv("PICOIN_MARKETPLACE_PAIRED_MINER_API_TOKEN") or legacy_api_token
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
        picoin_command=split_command(os.getenv("PICOIN_MARKETPLACE_PICOIN_MINER_COMMAND")),
        picoin_cwd=os.getenv("PICOIN_MARKETPLACE_PICOIN_MINER_CWD") or None,
        picoin_api_url=os.getenv("PICOIN_MARKETPLACE_PICOIN_MINER_API_URL") or None,
        picoin_api_token=os.getenv("PICOIN_MARKETPLACE_PICOIN_MINER_API_TOKEN") or None,
        paired_command=paired_command,
        paired_cwd=paired_cwd,
        paired_api_url=paired_api_url,
        paired_api_token=paired_api_token,
        require_picoin_miner=env_bool("PICOIN_MARKETPLACE_REQUIRE_PICOIN_MINER", False),
        picoin_capacity_percent=max(0, env_int("PICOIN_MARKETPLACE_PICOIN_CAPACITY_PERCENT", 10)),
        paired_capacity_percent=max(0, env_int("PICOIN_MARKETPLACE_PAIRED_CAPACITY_PERCENT", 90)),
        miner_command=legacy_command,
        miner_cwd=legacy_cwd,
        miner_api_url=legacy_api_url,
        miner_api_token=legacy_api_token,
        warmup_seconds=max(0, env_int("PICOIN_MARKETPLACE_MINER_WARMUP_SECONDS", 5)),
        restart_on_exit=env_bool("PICOIN_MARKETPLACE_MINER_RESTART_ON_EXIT", True),
        auto_create_pool=env_bool("PICOIN_MARKETPLACE_MINER_AUTO_CREATE_POOL", True),
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
    if config.auto_create_pool:
        created = worker_agent.json_post(
            f"{config.marketplace_url}/pools",
            {
                "hardware_type": config.hardware_type.value,
                "paired_coin": config.paired_coin,
                "name": f"{config.hardware_type.value.upper()} PICOIN/{config.paired_coin} pool",
                "picoin_capacity_percent": 10,
                "paired_capacity_percent": 90,
            },
        )
        pool_id = str(created.get("pool_id") or "").strip()
        if pool_id:
            return pool_id
    raise ValueError(f"active PICOIN/{config.paired_coin} {config.hardware_type.value} pool not found")


MinerProcesses = dict[str, subprocess.Popen[bytes] | None]


def paired_command(config: RealMinerConfig) -> list[str]:
    return config.paired_command or config.miner_command


def paired_cwd(config: RealMinerConfig) -> str | None:
    return config.paired_cwd or config.miner_cwd


def paired_api_url(config: RealMinerConfig) -> str | None:
    return config.paired_api_url or config.miner_api_url


def paired_api_token(config: RealMinerConfig) -> str | None:
    return config.paired_api_token or config.miner_api_token


def start_process(command: list[str], cwd: str | None) -> subprocess.Popen[bytes] | None:
    return subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def start_miner_processes(config: RealMinerConfig) -> MinerProcesses:
    if config.no_start:
        return {"picoin": None, "paired": None}
    effective_paired_command = paired_command(config)
    if config.require_picoin_miner and not config.picoin_command:
        raise ValueError("Picoin miner command is required; set PICOIN_MARKETPLACE_PICOIN_MINER_COMMAND")
    if config.require_picoin_miner and not effective_paired_command:
        raise ValueError("paired miner command is required; set PICOIN_MARKETPLACE_PAIRED_MINER_COMMAND")
    if not config.picoin_command and not effective_paired_command:
        raise ValueError(
            "at least one miner command is required; set "
            "PICOIN_MARKETPLACE_PICOIN_MINER_COMMAND and/or "
            "PICOIN_MARKETPLACE_PAIRED_MINER_COMMAND"
        )
    return {
        "picoin": start_process(config.picoin_command, config.picoin_cwd) if config.picoin_command else None,
        "paired": start_process(effective_paired_command, paired_cwd(config)) if effective_paired_command else None,
    }


def start_miner_process(config: RealMinerConfig) -> subprocess.Popen[bytes] | None:
    """Backward-compatible helper for legacy callers.

    New code should use start_miner_processes() so the Picoin and paired-coin
    miners can run at the same time.
    """
    if config.no_start:
        return None
    command = paired_command(config) or config.picoin_command
    if not command:
        raise ValueError("miner command is required")
    cwd = paired_cwd(config) if paired_command(config) else config.picoin_cwd
    return start_process(command, cwd)


def read_json_api(url: str | None, token: str | None = None) -> dict[str, Any]:
    if not url:
        return {}
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    timeout = float(os.getenv("PICOIN_MARKETPLACE_HTTP_TIMEOUT_SECONDS", "30"))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("miner API response is not an object")
    return payload


def read_miner_api(config: RealMinerConfig) -> dict[str, Any]:
    return read_json_api(paired_api_url(config), paired_api_token(config))


def read_picoin_miner_api(config: RealMinerConfig) -> dict[str, Any]:
    return read_json_api(config.picoin_api_url, config.picoin_api_token)


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


def parse_picoin_summary(summary: dict[str, Any]) -> dict[str, Any]:
    accepted = summary.get("accepted_shares", summary.get("accepted_jobs", summary.get("accepted")))
    rejected = summary.get("rejected_shares", summary.get("rejected_jobs", summary.get("rejected")))
    hashrate = summary.get("reported_hashrate", summary.get("hashrate"))
    uptime = summary.get("uptime_seconds", summary.get("uptime"))
    return {
        "reported_hashrate": float(hashrate) if isinstance(hashrate, int | float) else None,
        "accepted_shares": accepted if isinstance(accepted, int) else None,
        "rejected_shares": rejected if isinstance(rejected, int) else None,
        "uptime_seconds": uptime if isinstance(uptime, int) else None,
        "api": {"kind": "picoin", "summary": summary},
    }


def process_running(
    *,
    command: list[str],
    process: subprocess.Popen[bytes] | None,
    api_url: str | None,
    api_error: str | None,
    no_start: bool,
) -> bool:
    if process is not None:
        return process.poll() is None
    if api_url:
        return api_error is None
    if no_start:
        return True
    return not command


def process_metrics(
    *,
    label: str,
    command: list[str],
    process: subprocess.Popen[bytes] | None,
    api_url: str | None,
    api_error: str | None,
    no_start: bool,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "label": label,
        "configured": bool(command or api_url or process is not None),
        "started_by_agent": process is not None,
        "running": process_running(
            command=command,
            process=process,
            api_url=api_url,
            api_error=api_error,
            no_start=no_start,
        ),
    }
    if command:
        metrics["binary"] = command[0]
        metrics["arg_count"] = max(0, len(command) - 1)
    if process is not None:
        metrics["pid"] = process.pid
        metrics["exit_code"] = process.poll()
    if api_url:
        metrics["api_url"] = api_url
    if api_error:
        metrics["api_error"] = api_error
    return metrics


def miner_runtime_state(config: RealMinerConfig, processes: MinerProcesses) -> dict[str, Any]:
    picoin_process = processes.get("picoin")
    paired_process = processes.get("paired")
    effective_paired_command = paired_command(config)
    effective_paired_api_url = paired_api_url(config)
    metrics: dict[str, Any] = {
        "real_miner": True,
        "paired_controller": True,
        "paired_coin": config.paired_coin,
        "pair_symbol": f"PICOIN/{config.paired_coin}",
        "hardware_type": config.hardware_type.value,
        "split": {
            "picoin_percent": config.picoin_capacity_percent,
            "paired_percent": config.paired_capacity_percent,
        },
    }

    paired_parsed: dict[str, Any] = {
        "reported_hashrate": None,
        "accepted_shares": None,
        "rejected_shares": None,
        "uptime_seconds": None,
    }
    picoin_parsed: dict[str, Any] = {
        "reported_hashrate": None,
        "accepted_shares": None,
        "rejected_shares": None,
        "uptime_seconds": None,
    }
    paired_api_error = None
    picoin_api_error = None
    try:
        summary = read_miner_api(config)
        if summary:
            paired_parsed = parse_xmrig_summary(summary)
            metrics["paired_api"] = paired_parsed.pop("api")
    except Exception as exc:
        paired_api_error = str(exc)
    try:
        summary = read_picoin_miner_api(config)
        if summary:
            picoin_parsed = parse_picoin_summary(summary)
            metrics["picoin_api"] = picoin_parsed.pop("api")
    except Exception as exc:
        picoin_api_error = str(exc)

    picoin_metrics = process_metrics(
        label="picoin",
        command=config.picoin_command,
        process=picoin_process,
        api_url=config.picoin_api_url,
        api_error=picoin_api_error,
        no_start=config.no_start,
    )
    paired_metrics = process_metrics(
        label=config.paired_coin.lower(),
        command=effective_paired_command,
        process=paired_process,
        api_url=effective_paired_api_url,
        api_error=paired_api_error,
        no_start=config.no_start,
    )
    metrics["picoin"] = picoin_metrics
    metrics["paired"] = paired_metrics

    # Backward-compatible flat fields used by existing dashboards/tests.
    if effective_paired_command:
        metrics["miner_binary"] = effective_paired_command[0]
        metrics["miner_arg_count"] = max(0, len(effective_paired_command) - 1)
    elif config.picoin_command:
        metrics["miner_binary"] = config.picoin_command[0]
        metrics["miner_arg_count"] = max(0, len(config.picoin_command) - 1)
    if paired_process is not None:
        metrics["miner_pid"] = paired_process.pid
        metrics["miner_exit_code"] = paired_process.poll()
    elif picoin_process is not None:
        metrics["miner_pid"] = picoin_process.pid
        metrics["miner_exit_code"] = picoin_process.poll()
    if effective_paired_api_url:
        metrics["miner_api_url"] = effective_paired_api_url
    if paired_api_error:
        metrics["miner_api_error"] = paired_api_error

    required_running = [paired_metrics["running"]]
    if config.picoin_command or config.picoin_api_url or config.require_picoin_miner:
        required_running.append(picoin_metrics["running"])
    running = all(required_running)
    metrics["miner_running"] = running

    return {
        "metrics": metrics,
        "running": running,
        "reported_hashrate": paired_parsed["reported_hashrate"] or picoin_parsed["reported_hashrate"],
        "accepted_shares": paired_parsed["accepted_shares"] or picoin_parsed["accepted_shares"],
        "rejected_shares": paired_parsed["rejected_shares"] or picoin_parsed["rejected_shares"],
        "uptime_seconds": paired_parsed["uptime_seconds"] or picoin_parsed["uptime_seconds"],
    }


def worker_config_for_miner(
    config: RealMinerConfig,
    *,
    pool_id: str,
    processes: MinerProcesses,
) -> WorkerAgentConfig:
    state = miner_runtime_state(config, processes)
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


def stop_miner_processes(processes: MinerProcesses, *, timeout_seconds: float = 10.0) -> None:
    for label in ("paired", "picoin"):
        stop_miner_process(processes.get(label), timeout_seconds=timeout_seconds)


def restart_exited_processes(config: RealMinerConfig, processes: MinerProcesses) -> MinerProcesses:
    if not config.restart_on_exit or config.no_start:
        return processes
    updated = dict(processes)
    if config.picoin_command and updated.get("picoin") is not None and updated["picoin"].poll() is not None:
        updated["picoin"] = start_process(config.picoin_command, config.picoin_cwd)
    effective_paired_command = paired_command(config)
    if effective_paired_command and updated.get("paired") is not None and updated["paired"].poll() is not None:
        updated["paired"] = start_process(effective_paired_command, paired_cwd(config))
    return updated


def run_miner_once(
    config: RealMinerConfig,
    *,
    register: bool = True,
    worker_id: str | None = None,
    processes: MinerProcesses | None = None,
    process: subprocess.Popen[bytes] | None = None,
    stop_after: bool = True,
) -> dict[str, Any]:
    pool_id = discover_pool_id(config)
    started_processes = False
    if processes is None:
        if process is not None:
            processes = {"picoin": None, "paired": process}
        else:
            processes = start_miner_processes(config)
            started_processes = any(item is not None for item in processes.values())
        if started_processes and config.warmup_seconds:
            time.sleep(config.warmup_seconds)
    try:
        agent_config = worker_config_for_miner(config, pool_id=pool_id, processes=processes)
        result = worker_agent.run_once(agent_config, register=register, worker_id=worker_id)
        result["pool_id"] = pool_id
        result["miner"] = agent_config.metrics
        return result
    finally:
        if stop_after and started_processes:
            stop_miner_processes(processes)


def run_forever(config: RealMinerConfig, *, register: bool = True) -> None:
    pool_id = discover_pool_id(config)
    processes = start_miner_processes(config)
    worker_id = config.worker_id
    if any(item is not None for item in processes.values()) and config.warmup_seconds:
        time.sleep(config.warmup_seconds)
    try:
        while True:
            before = dict(processes)
            processes = restart_exited_processes(config, processes)
            if processes != before and config.warmup_seconds:
                time.sleep(config.warmup_seconds)
            try:
                agent_config = worker_config_for_miner(config, pool_id=pool_id, processes=processes)
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
        stop_miner_processes(processes)


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
    parser.add_argument("--picoin-command", default=None, help="Picoin miner command")
    parser.add_argument("--picoin-miner-cwd", default=None)
    parser.add_argument("--picoin-api-url", default=None)
    parser.add_argument("--picoin-api-token", default=None)
    parser.add_argument("--paired-command", default=None, help="paired-coin miner command, for example xmrig with pool args")
    parser.add_argument("--paired-miner-cwd", default=None)
    parser.add_argument("--paired-api-url", default=None)
    parser.add_argument("--paired-api-token", default=None)
    parser.add_argument("--command", default=None, help="legacy alias for --paired-command")
    parser.add_argument("--miner-cwd", default=None, help="legacy alias for --paired-miner-cwd")
    parser.add_argument("--xmrig-api-url", default=None, help="legacy alias for --paired-api-url")
    parser.add_argument("--xmrig-api-token", default=None, help="legacy alias for --paired-api-token")
    parser.add_argument("--require-picoin-miner", action="store_true", default=None)
    parser.add_argument("--warmup-seconds", type=int, default=None)
    parser.add_argument(
        "--no-auto-create-pool",
        action="store_true",
        default=None,
        help="fail if the PICOIN paired pool does not already exist",
    )
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
        picoin_command=split_command(args.picoin_command) or base.picoin_command,
        picoin_cwd=args.picoin_miner_cwd or base.picoin_cwd,
        picoin_api_url=args.picoin_api_url or base.picoin_api_url,
        picoin_api_token=args.picoin_api_token or base.picoin_api_token,
        paired_command=split_command(args.paired_command) or split_command(args.command) or base.paired_command,
        paired_cwd=args.paired_miner_cwd or args.miner_cwd or base.paired_cwd,
        paired_api_url=args.paired_api_url or args.xmrig_api_url or base.paired_api_url,
        paired_api_token=args.paired_api_token or args.xmrig_api_token or base.paired_api_token,
        require_picoin_miner=(
            base.require_picoin_miner if args.require_picoin_miner is None else args.require_picoin_miner
        ),
        miner_command=split_command(args.command) or base.miner_command,
        miner_cwd=args.miner_cwd or base.miner_cwd,
        miner_api_url=args.xmrig_api_url or base.miner_api_url,
        miner_api_token=args.xmrig_api_token or base.miner_api_token,
        warmup_seconds=max(0, args.warmup_seconds if args.warmup_seconds is not None else base.warmup_seconds),
        auto_create_pool=base.auto_create_pool if args.no_auto_create_pool is None else not args.no_auto_create_pool,
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
