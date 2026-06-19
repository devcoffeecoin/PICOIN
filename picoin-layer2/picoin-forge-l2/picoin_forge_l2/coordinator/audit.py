from __future__ import annotations

from pathlib import Path
from typing import Any

from picoin_forge_l2.common.hashing import canonical_json, sha256_text
from picoin_forge_l2.common.models import utc_now

from .storage import CoordinatorStorage


def build_event_export(state_dir: str | Path, *, limit: int = 1000) -> dict[str, Any]:
    events = list(reversed(CoordinatorStorage(state_dir).list_events(limit=limit)))
    rows = [event.model_dump(mode="json") for event in events]
    jsonl = "\n".join(canonical_json(row) for row in rows)
    if jsonl:
        jsonl += "\n"
    return {
        "schema_version": "picoin-forge-l2-event-export-v1",
        "event_count": len(rows),
        "generated_at": utc_now().isoformat(),
        "export_hash": sha256_text(jsonl),
        "jsonl": jsonl,
    }


def write_event_export(state_dir: str | Path, output_path: str | Path, *, limit: int = 1000) -> dict[str, Any]:
    export = build_event_export(state_dir, limit=limit)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(export["jsonl"], encoding="utf-8")
    return {
        "schema_version": export["schema_version"],
        "event_count": export["event_count"],
        "generated_at": export["generated_at"],
        "export_hash": export["export_hash"],
        "path": str(path),
    }
