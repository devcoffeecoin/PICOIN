from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .marketplace import DEFAULT_STATE_DIR, Marketplace
from .models import utc_now
from .scanner_worker import env_int


@dataclass(frozen=True)
class MaintenanceWorkerConfig:
    state_dir: Path = DEFAULT_STATE_DIR
    interval_seconds: int = 30
    stale_after_seconds: int = 120
    limit: int = 1000


def config_from_env() -> MaintenanceWorkerConfig:
    return MaintenanceWorkerConfig(
        state_dir=Path(os.getenv("PICOIN_MARKETPLACE_STATE_DIR", str(DEFAULT_STATE_DIR))),
        interval_seconds=max(1, env_int("PICOIN_MARKETPLACE_MAINTENANCE_INTERVAL_SECONDS", 30)),
        stale_after_seconds=max(0, env_int("PICOIN_MARKETPLACE_WORKER_STALE_AFTER_SECONDS", 120)),
        limit=max(1, env_int("PICOIN_MARKETPLACE_MAINTENANCE_LIMIT", 1000)),
    )


def run_maintenance_once(marketplace: Marketplace, config: MaintenanceWorkerConfig) -> dict[str, Any]:
    started_at = utc_now()
    result = marketplace.expire_stale_workers(
        stale_after_seconds=config.stale_after_seconds,
        limit=config.limit,
    )
    return {
        "service": "picoin-marketplace-maintenance",
        "started_at": started_at.isoformat(),
        "finished_at": utc_now().isoformat(),
        "result": result,
    }


def run_forever(config: MaintenanceWorkerConfig) -> None:
    marketplace = Marketplace(config.state_dir)
    while True:
        print(json.dumps(run_maintenance_once(marketplace, config), sort_keys=True), flush=True)
        time.sleep(config.interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Picoin Marketplace maintenance tasks.")
    parser.add_argument("--once", action="store_true", help="run one maintenance tick and exit")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--interval-seconds", type=int, default=None)
    parser.add_argument("--stale-after-seconds", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    return parser


def config_from_args(args: argparse.Namespace) -> MaintenanceWorkerConfig:
    base = config_from_env()
    return MaintenanceWorkerConfig(
        state_dir=Path(args.state_dir) if args.state_dir else base.state_dir,
        interval_seconds=max(1, args.interval_seconds or base.interval_seconds),
        stale_after_seconds=max(
            0,
            args.stale_after_seconds if args.stale_after_seconds is not None else base.stale_after_seconds,
        ),
        limit=max(1, args.limit or base.limit),
    )


def main() -> None:
    args = build_parser().parse_args()
    config = config_from_args(args)
    marketplace = Marketplace(config.state_dir)
    if args.once:
        print(json.dumps(run_maintenance_once(marketplace, config), indent=2, sort_keys=True))
        return
    run_forever(config)


if __name__ == "__main__":
    main()
