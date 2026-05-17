#!/usr/bin/env bash
set -euo pipefail

SERVER="${PICOIN_SERVER:-http://127.0.0.1:8000}"

python3 - "$SERVER" <<'PY'
import json
import sys
import urllib.error
import urllib.request

server = sys.argv[1].rstrip("/")
checks = [
    ("health", "/health"),
    ("sync", "/node/sync-status"),
    ("audit", "/audit/full"),
    ("checkpoint", "/node/checkpoints/latest"),
]

errors = []
for name, path in checks:
    url = f"{server}{path}"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        errors.append({"name": name, "ok": False, "detail": str(exc)})
        continue

    if name == "health":
        ok = payload.get("status") == "ok" and payload.get("chain", {}).get("valid") is True
        detail = f"status={payload.get('status')} height={payload.get('latest_block_height')}"
    elif name == "sync":
        ok = True
        detail = f"height={payload.get('latest_block_height')} peers={payload.get('peer_counts')}"
    elif name == "audit":
        ok = payload.get("valid") is True
        detail = f"valid={payload.get('valid')} issues={len(payload.get('issues', []))}"
    else:
        ok = True
        detail = "no checkpoint yet" if payload is None else f"height={payload.get('height')}"

    print(f"[{'ok' if ok else 'fail'}] {name}: {detail}")
    if not ok:
        errors.append({"name": name, "ok": False, "detail": detail})

if errors:
    print(json.dumps({"status": "fail", "errors": errors}, indent=2))
    raise SystemExit(1)

print(json.dumps({"status": "ok", "server": server}, indent=2))
PY
