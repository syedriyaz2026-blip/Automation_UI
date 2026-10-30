"""Manual handover and evidence bundle creation for failed local runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from platform_backend import PLATFORM
except ModuleNotFoundError:
    from Platform.backend.platform_backend import PLATFORM


SUCCESS_STATUS = "Registered - Pending Approval"


def category(error_type: str, message: str = "") -> str:
    text = f"{error_type} {message}".lower()
    if any(term in text for term in ("portal", "network", "timeout", "unavailable", "503", "502")):
        return "PORTAL_DOWNTIME_OR_NETWORK"
    if any(term in text for term in ("layout", "selector", "control", "not editable", "unexpected portal")):
        return "PORTAL_LAYOUT_CHANGE"
    if any(term in text for term in ("duplicate", "already registered", "already active", "member exists", "uan already")):
        return "DUPLICATE_UAN_OR_MEMBER"
    if any(term in text for term in ("mismatch", "identity", "does not match", "not the same")):
        return "DATA_MISMATCH"
    if any(term in text for term in ("validation", "required", "invalid format", "out of range", "must be")):
        return "VALIDATION_ERROR"
    return "AUTOMATION_OR_AUTHENTICATION_ERROR"


def create_handover(run_id: str, company_id: str, results: list[dict[str, Any]], message: str = "") -> dict[str, Any]:
    """Create an auditable handover package without credentials or secrets."""
    failures = [row for row in results if row.get("status") != SUCCESS_STATUS]
    evidence: list[str] = []
    clean_rows: list[dict[str, Any]] = []
    for row in failures:
        screenshot = row.get("screenshot", "") or row.get("Screenshot/Evidence", "")
        blob_screenshot = ""
        if screenshot and Path(screenshot).exists():
            blob_screenshot = PLATFORM.blobs.save_bytes(run_id, Path(screenshot).name, Path(screenshot).read_bytes())
            evidence.append(blob_screenshot)
        clean_rows.append({
            "row": row.get("row", row.get("Source Row", "")),
            "employee_code": row.get("employee_code", row.get("Employee Code", "")),
            "employee_name": row.get("employee_name", row.get("Employee Name", "")),
            "uan": row.get("uan", row.get("UAN", "")),
            "status": row.get("status", row.get("Status", "")),
            "error_type": row.get("error_type", row.get("Error Type", "")),
            "workflow_step": row.get("workflow_step", row.get("Workflow Step", "")),
            "message": row.get("message", row.get("Message", "")),
            "screenshot": blob_screenshot,
            "category": category(row.get("error_type", row.get("Error Type", "")), row.get("message", row.get("Message", ""))),
        })
    categories = sorted({row["category"] for row in clean_rows})
    bundle = {
        "run_id": run_id,
        "company_id": company_id,
        "status": "PENDING_MANUAL_HANDOVER",
        "message": message or "One or more rows require manual review before any retry.",
        "categories": categories,
        "affected_rows": clean_rows,
        "evidence": evidence,
        "retry_policy": "No automatic retry. Verify portal/member status manually before a new run.",
    }
    artifact = PLATFORM.blobs.save_json(run_id, "handover.json", bundle)
    bundle["artifact"] = artifact
    return bundle
