from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def append_event(path: Path, *, run_id: str, event: str, status: str, workflow_step: str = "", row: str = "", **extra):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "run_id": run_id, "row": row, "workflow_step": workflow_step, "event": event, "status": status, **extra}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
