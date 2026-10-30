"""Single-process API for EPFO member registration automation.

The service accepts employee records as JSON and runs the validated browser
workflow sequentially. REAL mode signs in through the employer portal and
stops before member approval. A private MOCK harness is available to tests.
"""

from __future__ import annotations

import json
import logging
import os
import re
import base64
import hashlib
import hmac
import secrets
import threading
import time
import uuid
import urllib.request
from email.parser import BytesParser
from email.policy import default as email_policy
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from Platform.backend.automation.playwright_page import PlaywrightPage, create_playwright_browser

from Platform.backend.data_processing.duplicate_checker import duplicate_errors
from Platform.backend.data_processing.validator import validate_record_all, validation_summary
from PF.backend.epfo_bot import ALIASES, EpfoBot, norm, read_records
from Platform.backend.authentication import AuthState, ensure_auth_environment
from PF.backend.mock_epfo_server import MockEpfoServer
try:
    # Keep direct legacy `import api_service` callers on the same platform
    # module instance they monkeypatch in tests and local scripts.
    from platform_backend import AUDIT_LOG_PATH, BLOB_ROOT, PLATFORM, SUCCESS_STATUSES
except ModuleNotFoundError:
    from Platform.backend.platform_backend import AUDIT_LOG_PATH, BLOB_ROOT, PLATFORM, SUCCESS_STATUSES
from Platform.backend.configuration import validate_configuration
from Platform.backend.handover import category as handover_category
from Platform.backend.handover import create_handover
from Platform.backend.reporting.email_reporter import send_failure_email
from Platform.backend.reporting.run_report import generate_run_report
from ESIC.backend.reporting.esi_report import build_esi_report, json_bytes as esi_json_bytes, workbook_bytes as esi_workbook_bytes
from Platform.backend.source_data import SOURCE_DATA, SourceDataError
from ESIC.backend.esi_data import ESI_FIELDS, ESI_REQUIRED_FIELDS, FIELD_LABELS, esi_contract
from ESIC.backend.esi_store import ESI_STORE
from ESIC.backend.esi_source import ESI_SOURCE_DATA
from ESIC.backend.esi_automation import ESI_AUTOMATION
from Platform.backend.openapi_spec import build_openapi, swagger_html
from Platform.backend.credential_vault import CredentialVault
from Platform.backend.rbac import (
    PERMISSIONS,
    ROLE_MASTER_ADMIN,
    ROLE_ADMIN,
    ROLE_DEFAULT_PERMISSIONS,
    ROLE_SUPER_ADMIN,
    ROLE_USER,
    ROLES,
    USER_STORE,
    default_module_access,
    default_permissions,
)
from Platform.backend.scope_intake_settings import get as get_intake_settings
from Platform.backend.scope_intake_settings import list_for_companies as list_intake_settings
from Platform.backend.scope_intake_settings import save as save_intake_settings


ROOT = Path(__file__).resolve().parent
PF_ROOT = ROOT.parents[1] / "PF" / "backend"
RUN_ROOT = ROOT / "api_runs"
MAX_BODY_BYTES = 10 * 1024 * 1024
TERMINAL_STATUSES = {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "STOPPED", "HANDOVER_REQUIRED", "RECOVERY_REQUIRED"}
ALLOWED_OPTIONS = {
    "mode", "scenario", "headless", "demo_delay_ms", "typing_delay_ms",
    "confirm_real", "company_id", "company_name", "deputed_company_id",
    "deputed_company_name", "record_ids", "source", "auto_process", "auth_only",
}
EXECUTION_LOCK = threading.Lock()
LOGGER = logging.getLogger("epfo_api")
CREDENTIAL_LOCK = threading.RLock()
SESSION_CREDENTIALS: dict[str, dict[str, str]] = {}
CREDENTIAL_VAULT = CredentialVault(ROOT / "config" / "credential_vault.json")
SCHEDULES_PATH = ROOT / "api_schedules.json"
UPLOAD_LOG_PATH = ROOT / "upload_logs.jsonl"
FIELDS = [
    "sl_no", "emp_code", "company", "name", "doj", "dob", "aadhaar",
    "father_name", "marital_status", "husband_wife_name", "gender", "mobile",
    "email", "location", "basic_wage", "uan",
]
PF_REQUIRED_FIELDS = ["uan", "aadhaar", "name", "dob", "doj", "basic_wage"]
JWT_COOKIE = "EPFO_JWT"
JWT_ISSUER = "pf-reg-auto-local"

def log_upload_event(*, company_id: str, deputed_company_id: str = "", source: str, filename: str, digest: str, status: str, total: int, valid: int, invalid: int, message: str = "") -> None:
    UPLOAD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    event = {"timestamp": now(), "event": "DATA_INTAKE", "company_id": company_id, "deputed_company_id": deputed_company_id, "source": source, "filename": filename, "sha256": digest, "status": status, "total": total, "valid": valid, "invalid": invalid, "message": message}
    with UPLOAD_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")

JWT_RUNTIME_SECRET = secrets.token_urlsafe(32)
SECURITY_CONFIG_PATH = ROOT / "config" / "security.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def esi_pending_automation(
    company_id: str,
    deputed_company_id: str,
    *,
    total: int = 0,
    valid: int = 0,
    invalid: int = 0,
    client_id: str = "",
    state: str = "",
    district: str = "",
) -> dict[str, Any]:
    """Return an explicit ESI hand-off state without invoking the PF worker.

    The supplied ESIC archive contains the portal trace but omits the Java
    implementation.  Keeping this state explicit prevents ESI records from
    accidentally entering the EPFO worker while still exposing the accepted
    and rejected counts to the UI.
    """
    return ESI_AUTOMATION.pending(
        company_id=company_id,
        deputed_company_id=deputed_company_id,
        total=total,
        valid=valid,
        invalid=invalid,
        client_id=client_id,
        state=state,
        district=district,
    )


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _security_config() -> dict[str, Any]:
    try:
        return json.loads(SECURITY_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"access_token_ttl_seconds": 3600, "issuer": JWT_ISSUER}


def _jwt_secret() -> bytes:
    configured = os.getenv("EPFO_JWT_SECRET", "")
    return (configured or JWT_RUNTIME_SECRET).encode("utf-8")


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_access_token(
    username: str,
    roles: list[str] | None = None,
    company_ids: list[str] | None = None,
    permissions: list[str] | None = None,
    module_access: list[str] | None = None,
) -> tuple[str, int]:
    config = _security_config()
    ttl = max(60, int(config.get("access_token_ttl_seconds", 3600)))
    issued = int(datetime.now(timezone.utc).timestamp())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": username,
        "iss": str(config.get("issuer", JWT_ISSUER)),
        "iat": issued,
        "exp": issued + ttl,
        "roles": roles or [os.getenv("EPFO_UI_ROLE", "operator")],
        "company_ids": company_ids or ["*"],
        "permissions": permissions or [],
        "module_access": module_access or [],
    }
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = _b64url(hmac.new(_jwt_secret(), signing_input, hashlib.sha256).digest())
    return f"{encoded_header}.{encoded_payload}.{signature}", ttl


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        encoded_header, encoded_payload, signature = token.split(".")
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        expected = _b64url(hmac.new(_jwt_secret(), signing_input, hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        header = json.loads(_b64decode(encoded_header).decode("utf-8"))
        payload = json.loads(_b64decode(encoded_payload).decode("utf-8"))
        config = _security_config()
        if header.get("alg") != "HS256" or payload.get("iss") != str(config.get("issuer", JWT_ISSUER)):
            return None
        if not payload.get("sub") or int(payload.get("exp", 0)) <= int(datetime.now(timezone.utc).timestamp()):
            return None
        return payload
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError, base64.binascii.Error):
        return None


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def source_text(value: Any) -> str:
    """Preserve source whitespace so validation can report it explicitly."""
    return "" if value is None else str(value)


def _as_source_id(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _as_optional_scope_id(value: Any) -> int | None:
    """Parse a deputed-company scope, where 0 is Emanage's ALL value."""
    try:
        parsed = int(str(value).strip())
        return parsed if parsed >= 0 else None
    except (TypeError, ValueError):
        return None


def _as_source_datetime(value: Any, label: str = "source timestamp") -> datetime | None:
    raw = text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _safe_int_query(value: Any, default: int) -> int:
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return default


def normalize_record(raw: dict[str, Any], index: int) -> dict[str, str]:
    """Map API keys using the same aliases as the existing Excel importer."""
    by_name = {norm(key): value for key, value in raw.items()}
    record: dict[str, str] = {}
    for field in FIELDS:
        aliases = [field, *ALIASES.get(field, [])]
        value = ""
        for alias in aliases:
            if norm(alias) in by_name:
                value = source_text(by_name[norm(alias)])
                break
        record[field] = value
    record["row_number"] = text(raw.get("row_number") or raw.get("source_row") or index + 1)
    record["status"] = "Queued"
    return record


def result_for(record: dict[str, str], status: str, message: str, *, error_type: str = "", step: str = "", screenshot: str = "") -> dict[str, Any]:
    return {
        "record_id": record.get("record_id", ""),
        "company_id": record.get("company_id", ""),
        "deputed_company_id": record.get("deputed_company_id", ""),
        "company": record.get("company", ""),
        "deputed_company": record.get("deputed_company", ""),
        "source": record.get("source", ""),
        "row": record["row_number"],
        "employee_code": record.get("emp_code", ""),
        "employee_name": record.get("name", ""),
        "uan": record.get("uan", ""),
        "status": status,
        "error_type": error_type,
        "workflow_step": step,
        "message": message,
        "screenshot": screenshot,
        "completed_at": now(),
    }


def error_response(code: str, message: str, *, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


def configured_max_batch_records() -> int:
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    return int(settings.get("max_batch_records", 500))


class RunStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.runs: dict[str, dict[str, Any]] = {}
        self.load_persisted()

    def load_persisted(self) -> None:
        """Load history and fail closed after a service restart.

        A browser session cannot be safely reconstructed after a process crash,
        so non-terminal runs become RECOVERY_REQUIRED instead of being retried.
        This prevents duplicate member submissions.
        """
        if not RUN_ROOT.exists():
            return
        for path in RUN_ROOT.glob("*/run.json"):
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                saved.setdefault("events", [])
                saved.setdefault("results", [])
                saved.setdefault("records", [])
                saved.setdefault("options", {})
                saved.setdefault("total", len(saved["records"]))
                saved.setdefault("completed", len(saved["results"]))
                saved["stop_event"] = threading.Event()
                saved["continue_event"] = threading.Event()
                if saved.get("status") not in TERMINAL_STATUSES:
                    saved["status"] = "RECOVERY_REQUIRED"
                    saved["message"] = "Service restarted before completion; no automatic retry was made. Review the run evidence before starting a new run."
                    saved["updated_at"] = now()
                    recovery_company_id = text(saved.get("options", {}).get("company_id"))
                    if recovery_company_id:
                        scoped_rows = {
                            text(item.get("record_id")): item
                            for item in PLATFORM.records(
                                recovery_company_id,
                                deputed_company_id=text(saved.get("options", {}).get("deputed_company_id")),
                            )
                        }
                        for pending_record in saved.get("records", []):
                            record_id = text(pending_record.get("record_id"))
                            if record_id and scoped_rows.get(record_id, {}).get("status") not in SUCCESS_STATUSES:
                                PLATFORM.update_status(
                                    recovery_company_id,
                                    record_id,
                                    "RECOVERY_REQUIRED",
                                    run_id=saved.get("run_id", ""),
                                    message="Service restarted before this record reached a terminal state. Review evidence before retrying.",
                                )
                self.runs[saved["run_id"]] = saved
            except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
                LOGGER.warning("Ignoring unreadable run state %s: %s", path, exc)

    def create(self, records: list[dict[str, str]], options: dict[str, Any]) -> dict[str, Any]:
        run_id = "RUN-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6].upper()
        run = {
            "run_id": run_id,
            "created_at": now(),
            "updated_at": now(),
            "status": "QUEUED",
            "mode": options.get("mode", "REAL").upper(),
            "stop_before_approval": True,
            "total": len(records),
            "completed": 0,
            "current_row": "",
            "current_employee": "",
            "auth_state": "",
            "message": "Queued for sequential processing.",
            "records": records,
            "results": [],
            "events": [],
            "options": {k: v for k, v in options.items() if k in ALLOWED_OPTIONS},
            "stop_event": threading.Event(),
            "continue_event": threading.Event(),
        }
        with self.lock:
            self.runs[run_id] = run
        self.event(run_id, "RUN_CREATED", "QUEUED", message=run["message"])
        return run

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self.runs.get(run_id)

    def event(self, run_id: str, event: str, status: str, **extra: Any) -> None:
        run = self.get(run_id)
        if not run:
            return
        entry = {"timestamp": now(), "event": event, "status": status, **extra}
        with self.lock:
            run["events"].append(entry)
            run["updated_at"] = entry["timestamp"]
            run["status"] = status
            if "message" in extra:
                run["message"] = extra["message"]
            self._persist(run)
            self._append_jsonl(run_id, "audit.jsonl", entry)

    def persist_error_log(self, run_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Persist a sanitized, row-level error log for operations and review."""
        failures = [item for item in results if item.get("status") != "Registered - Pending Approval"]
        errors = []
        for item in failures:
            error_type = item.get("error_type", item.get("Error Type", ""))
            message = item.get("message", item.get("Message", ""))
            errors.append({
                "row": item.get("row", item.get("Source Row", "")),
                "employee_code": item.get("employee_code", item.get("Employee Code", "")),
                "employee_name": item.get("employee_name", item.get("Employee Name", "")),
                "uan": item.get("uan", item.get("UAN", "")),
                "status": item.get("status", item.get("Status", "")),
                "error_type": error_type,
                "category": handover_category(error_type, message),
                "workflow_step": item.get("workflow_step", item.get("Workflow Step", "")),
                "message": message,
                "screenshot": item.get("screenshot", item.get("Screenshot/Evidence", "")),
                "recorded_at": item.get("completed_at", now()),
            })
        target = RUN_ROOT / run_id / "errors.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"run_id": run_id, "updated_at": now(), "count": len(errors), "errors": errors}
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)
        lines = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in errors)
        self._write_text_atomic(RUN_ROOT / run_id / "error_log.jsonl", lines)
        return payload

    def _append_jsonl(self, run_id: str, name: str, entry: dict[str, Any]) -> None:
        target = RUN_ROOT / run_id / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_text_atomic(target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(target)

    def _persist(self, run: dict[str, Any]) -> None:
        if run.get("status") in TERMINAL_STATUSES:
            try:
                run["report"] = generate_run_report(run)
            except Exception as exc:
                LOGGER.warning("Could not generate report for %s: %s", run.get("run_id"), exc)
        target = RUN_ROOT / run["run_id"] / "run.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        safe = {k: v for k, v in run.items() if k not in {"stop_event", "continue_event"}}
        temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(safe, indent=2, ensure_ascii=False), encoding="utf-8")
            for attempt in range(8):
                try:
                    temp.replace(target)
                    break
                except PermissionError:
                    if attempt == 7:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            temp.unlink(missing_ok=True)

    def snapshot(self, run_id: str) -> dict[str, Any] | None:
        run = self.get(run_id)
        if not run:
            return None
        with self.lock:
            return {k: v for k, v in run.items() if k not in {"stop_event", "continue_event"}}


STORE = RunStore()


def attempt_handover_email(run_id: str, *, force: bool = False) -> dict[str, Any]:
    """Attempt only the handover notification; never retry browser work."""
    run = STORE.get(run_id)
    if not run:
        return {"attempted": False, "sent": False, "error": "Run not found."}
    if not run.get("handover"):
        return {"attempted": False, "sent": False, "error": "No handover is available for this run."}
    previous = run.get("handover_email") or {}
    if previous.get("sent"):
        return previous
    if run.get("handover_email_attempted") and not force:
        return previous or {"attempted": True, "sent": False}

    run["handover_email_attempted"] = True
    try:
        sent = send_failure_email(run["results"], run_id, LOGGER.info)
        result = {"attempted": True, "sent": bool(sent)}
    except Exception as exc:
        LOGGER.exception("Handover email failed for run %s", run_id)
        result = {"attempted": True, "sent": False, "error": str(exc)}
    run["handover_email"] = result
    event = "HANDOVER_EMAIL_SENT" if result["sent"] else "HANDOVER_EMAIL_NOT_SENT"
    message = "Handover email accepted by SMTP." if result["sent"] else result.get("error", "SMTP delivery was not accepted.")
    STORE.event(run_id, event, run.get("status", "HANDOVER_REQUIRED"), message=message)
    return result


class SourceScheduler:
    """Local continuous API synchronizer; it never inserts into EPFO."""
    def __init__(self):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.schedules = self._load()
        self.thread = threading.Thread(target=self._loop, name="api-source-scheduler", daemon=True)
        self.thread.start()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not SCHEDULES_PATH.exists():
            return {}
        try:
            return json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _persist(self):
        temp = SCHEDULES_PATH.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.schedules, indent=2), encoding="utf-8")
        temp.replace(SCHEDULES_PATH)

    def list(self):
        with self.lock:
            return list(self.schedules.values())

    def upsert(self, company_id: str, interval_seconds: int, source_url: str = ""):
        PLATFORM.config(company_id)
        if interval_seconds < 30 or interval_seconds > 86400:
            raise ValueError("interval_seconds must be between 30 and 86400.")
        item = {"schedule_id": f"SCHED-{uuid.uuid4().hex[:8].upper()}", "company_id": company_id, "interval_seconds": interval_seconds, "source_url": source_url, "enabled": True, "last_run": "", "last_message": "Not run yet."}
        with self.lock:
            self.schedules[item["schedule_id"]] = item
            self._persist()
        return item

    def remove(self, schedule_id: str) -> bool:
        with self.lock:
            removed = self.schedules.pop(schedule_id, None) is not None
            if removed:
                self._persist()
            return removed

    def _loop(self):
        while not self.stop_event.wait(5):
            now_epoch = datetime.now(timezone.utc).timestamp()
            with self.lock:
                items = list(self.schedules.values())
            for item in items:
                if not item.get("enabled"):
                    continue
                last = item.get("last_run_epoch", 0) or 0
                if now_epoch - last < int(item["interval_seconds"]):
                    continue
                self.sync(item)

    def sync(self, item: dict[str, Any]) -> None:
        item["last_run"] = now()
        item["last_run_epoch"] = datetime.now(timezone.utc).timestamp()
        try:
            url = item.get("source_url") or PLATFORM.config(item["company_id"]).get("source_url", "")
            if not url:
                item["last_message"] = "No source_url configured; waiting for API ingestion."
                return
            parsed = urlparse(url)
            if parsed.scheme not in {"https", "http"} or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"}):
                raise ValueError("Source URL must use HTTPS, or HTTP localhost for local testing.")
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=20) as response:
                payload = json.loads(response.read(MAX_BODY_BYTES).decode("utf-8"))
            rows = payload.get("employees", payload) if isinstance(payload, dict) else payload
            result = PLATFORM.ingest(item["company_id"], rows, source="scheduled_api")
            item["last_message"] = f"Synced {result['total']} inbox rows; insertion still requires validation and Start Insertion."
        except Exception as exc:
            item["last_message"] = f"Sync failed: {exc}"
        finally:
            with self.lock:
                self.schedules[item["schedule_id"]] = item
                self._persist()


SCHEDULER = SourceScheduler()
AUTO_PROCESS_ENABLED = os.getenv("PF_AUTO_PROCESS_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
AUTO_PROCESS_LOCKS: dict[str, threading.Lock] = {}
AUTO_PROCESS_LOCKS_GUARD = threading.Lock()



def validate_payload(payload: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    raw_records = payload.get("employees", payload.get("records"))
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("employees must be a non-empty JSON array.")
    max_batch = configured_max_batch_records()
    if len(raw_records) > max_batch:
        raise ValueError(f"Batch contains {len(raw_records)} records; the configured maximum is {max_batch}.")
    allowed_fields = {norm(alias) for field in FIELDS for alias in [field, *ALIASES.get(field, [])]}
    allowed_fields.update({"rownumber", "sourcerow"})
    unsupported = sorted({str(key) for item in raw_records if isinstance(item, dict) for key in item if norm(key) not in allowed_fields})
    if unsupported:
        raise ValueError("Unsupported employee fields: " + ", ".join(unsupported))
    records = [normalize_record(item, index) for index, item in enumerate(raw_records) if isinstance(item, dict)]
    if len(records) != len(raw_records):
        raise ValueError("Every employees item must be a JSON object.")
    duplicates = duplicate_errors(records)
    failures: list[dict[str, Any]] = []
    for record in records:
        errors = validate_record_all(record)
        if record["row_number"] in duplicates:
            errors.append({"cell": f"row {record['row_number']}", "message": duplicates[record["row_number"]]})
        if errors:
            failures.append(result_for(record, "Validation Failed", validation_summary(errors), error_type="Input Data Validation", step="API pre-submission validation"))
    return records, failures


def load_error_log(run_id: str, run: dict[str, Any] | None = None) -> dict[str, Any]:
    target = RUN_ROOT / run_id / "errors.json"
    if target.exists():
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    results = (run or {}).get("results", [])
    errors = []
    for item in results:
        if item.get("status") == "Registered - Pending Approval":
            continue
        error_type = item.get("error_type", item.get("Error Type", ""))
        message = item.get("message", item.get("Message", ""))
        errors.append({
            "row": item.get("row", item.get("Source Row", "")),
            "employee_code": item.get("employee_code", item.get("Employee Code", "")),
            "employee_name": item.get("employee_name", item.get("Employee Name", "")),
            "uan": item.get("uan", item.get("UAN", "")),
            "status": item.get("status", item.get("Status", "")),
            "error_type": error_type,
            "category": handover_category(error_type, message),
            "workflow_step": item.get("workflow_step", item.get("Workflow Step", "")),
            "message": message,
            "screenshot": item.get("screenshot", item.get("Screenshot/Evidence", "")),
            "recorded_at": item.get("completed_at", ""),
        })
    return {"run_id": run_id, "updated_at": now(), "count": len(errors), "errors": errors}


def api_preflight(mode: str, artifact_dir: Path) -> list[dict[str, str]]:
    """Run deterministic checks before an API-triggered browser session."""
    checks: list[dict[str, str]] = []

    def check(name: str, passed: bool, message: str):
        checks.append({"name": name, "status": "PASS" if passed else "FAIL", "message": message})

    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    settings["auth_mode"] = mode
    config_report = validate_configuration()
    check("Configuration and selector contracts", config_report["valid"], "; ".join(config_report["errors"]) or "All local configuration contracts are valid.")
    try:
        ensure_auth_environment(settings, mode)
        check("Authentication mode", True, mode)
    except Exception as exc:
        check("Authentication mode", False, str(exc))

    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        probe = artifact_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        check("Artifact directory writable", True, str(artifact_dir))
    except Exception as exc:
        check("Artifact directory writable", False, str(exc))

    try:
        check("Playwright browser available", True, "Playwright browser adapter is configured")
    except Exception as exc:
        check("Chromium executable available", False, str(exc))

    try:
        selectors = json.loads((ROOT / "config" / "selectors.json").read_text(encoding="utf-8"))
        critical = ("login_username", "login_password", "login_submit", "captcha", "captcha_input", "uan", "aadhaar", "verify", "joining_date", "wage", "save")
        missing = [key for key in critical if not selectors.get(key)]
        check("Critical selector configuration", not missing, "Missing: " + ", ".join(missing))
    except Exception as exc:
        check("Critical selector configuration", False, str(exc))

    if mode == "REAL":
        try:
            from rapidocr import RapidOCR  # noqa: F401
            import cv2  # noqa: F401
            from PIL import Image  # noqa: F401
            model = ROOT.parents[1] / "PF" / "backend" / "models" / "ocr-glyphs-python.json"
            check("CAPTCHA OCR available", model.is_file(), "Trained glyph model, Pillow, RapidOCR, and OpenCV installed" if model.is_file() else "Trained glyph model is missing")
        except ImportError as exc:
            check("CAPTCHA OCR available", False, f"Install backend requirements: {type(exc).__name__}")
        try:
            request = urllib.request.Request(settings["portal_url"], method="GET", headers={"User-Agent": "EPFO-Automation-Preflight/1.0"})
            with urllib.request.urlopen(request, timeout=10) as response:
                check("EPFO portal reachable", response.status < 500, f"HTTP {response.status}")
        except Exception as exc:
            check("EPFO portal reachable", False, str(exc))
    else:
        check("Mock endpoint isolation", True, "MOCK uses a generated 127.0.0.1 endpoint")
    return checks


def run_worker(run_id: str, username: str, password: str) -> None:
    run = STORE.get(run_id)
    if not run:
        return
    options = run["options"]
    mode = run["mode"]
    company_id = text(options.get("company_id"))
    deputed_company_id = text(options.get("deputed_company_id"))
    if company_id:
        username, password = resolve_credentials(company_id, username, password, deputed_company_id)
        company_config = _company_config_or_empty(company_id)
    else:
        company_config = {}
    artifact_dir = RUN_ROOT / run_id / "artifacts"
    audit_path = RUN_ROOT / run_id / "audit.jsonl"
    mock_server = None
    browser = None
    current_record: dict[str, Any] | None = None

    def refresh_handover() -> None:
        run["handover_required"] = True
        STORE.persist_error_log(run_id, run["results"])
        run["handover"] = create_handover(run_id, company_id, run["results"], "Manual review is required before any retry or correction.")

    def notify_handover() -> None:
        attempt_handover_email(run_id)

    def mark_unfinished(status: str, message: str, *, exclude_record_id: str = "") -> None:
        """Close every row that was reserved by this run but not completed.

        A run is a reservation over a concrete record set. If setup, login, or
        the worker itself fails, leaving the remaining rows AUTO_QUEUED would
        make them look healthy while also preventing a safe retry. Persist an
        explicit retryable state for every untouched row instead.
        """
        if not company_id:
            return
        scoped_rows = {
            text(item.get("record_id")): item
            for item in PLATFORM.records(company_id, deputed_company_id=deputed_company_id)
        }
        for pending_record in run.get("records", []):
            record_id = text(pending_record.get("record_id"))
            if not record_id or record_id == exclude_record_id:
                continue
            if scoped_rows.get(record_id, {}).get("status") in SUCCESS_STATUSES:
                continue
            PLATFORM.update_status(company_id, record_id, status, run_id=run_id, message=message)

    try:
        with EXECUTION_LOCK:
            STORE.event(run_id, "VALIDATION_COMPLETED", "VALIDATING", message="API records validated before browser submission.")
            results = list(run["results"])
            invalid_rows = {item["row"] for item in results}
            valid = [record for record in run["records"] if record["row_number"] not in invalid_rows]
            run["results"] = results
            if company_id:
                for item in results:
                    if item.get("record_id") and item.get("status") == "Validation Failed":
                        PLATFORM.update_status(company_id, item["record_id"], "VALIDATION_FAILED", run_id=run_id, message=item.get("message", ""))
            if results:
                refresh_handover()
            if not valid and not options.get("auth_only"):
                refresh_handover()
                notify_handover()
                STORE.event(run_id, "RUN_COMPLETED_WITH_ERRORS", "HANDOVER_REQUIRED", message="No valid rows were submitted; validation handover is ready.")
                return

            if True:
                preflight = api_preflight(mode, artifact_dir)
                for item in preflight:
                    STORE.event(run_id, "PREFLIGHT", "PREFLIGHT", check=item["name"], check_status=item["status"], message=item["message"])
                failures = [item for item in preflight if item["status"] != "PASS"]
                if failures:
                    message = "; ".join(f"{item['name']}: {item['message']}" for item in failures)
                    run["results"].append({"row": "", "employee_name": "", "status": "Preflight Failed", "error_type": "Configuration or Portal Preflight", "workflow_step": "Preflight", "message": message, "screenshot": "", "completed_at": now()})
                    mark_unfinished("FAILED", f"Run preflight failed before browser submission: {message}")
                    refresh_handover()
                    notify_handover()
                    STORE.event(run_id, "RUN_FAILED", "FAILED", message=message, error_type="Configuration or Portal Preflight")
                    return
                browser = create_playwright_browser(headless=bool(options.get("headless", False)))
                page = PlaywrightPage(browser.page)
                # Source-backed companies are intentionally not required to be
                # present in the local demo registry. Prefer the resolved
                # scope names carried by the run so evidence folders and
                # portal context never fall back to a numeric source ID.
                resolved_company_name = options.get("company_name") or company_config.get("name") or company_id
                resolved_deputed_name = options.get("deputed_company_name") or company_config.get("deputed_company_name") or deputed_company_id
                bot = EpfoBot(page, lambda message: STORE.event(run_id, "LOG", run["status"], message=message), artifact_dir, company_name=resolved_company_name, deputed_company_name=resolved_deputed_name)
                if company_config:
                    bot.set_selector_profile(company_config.get("selector_profile", "EPFO_EMPLOYER_DEFAULT"))
                bot.settings["auth_mode"] = mode
                bot.settings["stop_before_approval"] = True
                if company_config:
                    bot.settings["portal_url"] = company_config.get("portal_url", bot.settings["portal_url"])
                    bot.settings["register_url"] = company_config.get("register_url", bot.settings["register_url"])
                # Normal-speed local demonstration defaults; callers can still
                # override them per run with demo_delay_ms/typing_delay_ms.
                bot.settings["mock_demo_delay_ms"] = int(options.get("demo_delay_ms", 450) or 0)
                bot.settings["mock_typing_delay_ms"] = int(options.get("typing_delay_ms", 70) or 0)

                if mode == "MOCK":
                    mock_server = MockEpfoServer(str(options.get("scenario", "SUCCESS")))
                    bot.mock_base_url = mock_server.start()
                    bot.login_page(username or "TEST_EMPLOYER", password or "TEST_PASSWORD", f"{bot.mock_base_url}/login")
                else:
                    if not username or not password:
                        message = f"No PF credentials configured for company {company_id or 'selected profile'}. Configure session credentials or the company environment variables."
                        run["results"].append({"row": "", "employee_name": "", "status": "Authentication Failed", "error_type": "Company Credentials Missing", "workflow_step": "Credential preflight", "message": message, "screenshot": "", "completed_at": now()})
                        refresh_handover()
                        notify_handover()
                        STORE.event(run_id, "RUN_FAILED", "FAILED", message=message, error_type="Company Credentials Missing")
                        return
                    bot.login_page(username, password)

                def auth_state(state: AuthState, message: str):
                    run["auth_state"] = state.value
                    screenshot = getattr(bot, "last_auth_screenshot", "")
                    extra = {"state": state.value, "message": message}
                    if screenshot:
                        extra["screenshot"] = screenshot
                    STORE.event(run_id, "AUTH_STATE", "AUTHENTICATING", **extra)

                STORE.event(run_id, "AUTHENTICATION_STARTED", "AUTHENTICATING", message="Browser opened; CAPTCHA recognition and Sign In will run automatically.")
                auth = bot.authenticate(on_state=auth_state, continue_event=run["continue_event"])
                if auth.state != AuthState.REGISTRATION_READY:
                    item = {"row": "", "employee_name": "", "status": "Authentication Failed", "error_type": auth.error_type, "workflow_step": auth.state.value, "message": auth.message, "screenshot": auth.screenshot, "completed_at": now()}
                    run["results"].append(item)
                    if options.get("auth_only"):
                        STORE.event(run_id, "RUN_FAILED", "FAILED", message=auth.message, error_type=auth.error_type)
                        return
                    if company_id:
                        for pending_record in valid:
                            PLATFORM.update_status(
                                company_id,
                                pending_record.get("record_id", ""),
                                "FAILED",
                                run_id=run_id,
                                message=f"Authentication failed before this row was submitted: {auth.message}",
                            )
                    refresh_handover()
                    notify_handover()
                    STORE.event(run_id, "RUN_FAILED", "FAILED", message=auth.message, error_type=auth.error_type)
                    return

                if options.get("auth_only"):
                    STORE.event(run_id, "RUN_COMPLETED", "COMPLETED", message="PF sign-in test passed; no employee records were submitted.")
                    return

                STORE.event(run_id, "AUTHENTICATED", "PROCESSING", message="Authentication checkpoint completed; registration starts sequentially.")
                for record in valid:
                    current_record = record
                    if run["stop_event"].is_set():
                        STORE.event(run_id, "RUN_STOPPED", "STOPPED", message="Stopped at a safe row boundary.")
                        break
                    run["current_row"] = record["row_number"]
                    run["current_employee"] = record.get("name", "")
                    if company_id:
                        PLATFORM.update_status(
                            company_id,
                            record.get("record_id", ""),
                            "PROCESSING",
                            run_id=run_id,
                            message="Browser submission started for this record.",
                        )
                    STORE.event(run_id, "ROW_STARTED", "PROCESSING", row=record["row_number"], employee=record.get("name", ""), message=f"Processing {record.get('name', '')}.")
                    result = bot.register_one(record)
                    item = result_for(record, result.status, result.message, error_type=result.error_type, step=result.workflow_step, screenshot=result.screenshot)
                    run["results"].append(item)
                    run["completed"] += 1
                    if company_id:
                        inbox_status = "Registered - Pending Approval" if result.status == "Registered - Pending Approval" else "FAILED"
                        PLATFORM.update_status(company_id, record.get("record_id", ""), inbox_status, run_id=run_id, message=result.message)
                    if result.status != "Registered - Pending Approval":
                        refresh_handover()
                    STORE.event(run_id, "ROW_COMPLETED" if result.status == "Registered - Pending Approval" else "ROW_FAILED", "PROCESSING", row=record["row_number"], employee=record.get("name", ""), result=item, message=result.message)
                if run.get("handover_required"):
                    notify_handover()
                    STORE.event(run_id, "RUN_COMPLETED_WITH_ERRORS", "HANDOVER_REQUIRED", message="Rows completed with errors; manual handover is ready. Successful rows remain pending approval.")
                else:
                    STORE.event(run_id, "RUN_COMPLETED", "COMPLETED", message="All eligible rows processed; approval was not automated.")
    except Exception as exc:
        LOGGER.exception("Run %s failed", run_id)
        current_status = ""
        if company_id and current_record:
            current_status = next(
                (
                    text(item.get("status"))
                    for item in PLATFORM.records(company_id, deputed_company_id=deputed_company_id)
                    if text(item.get("record_id")) == text(current_record.get("record_id"))
                ),
                "",
            )
        if company_id and current_record and current_status not in SUCCESS_STATUSES:
            PLATFORM.update_status(
                company_id,
                current_record.get("record_id", ""),
                "FAILED",
                run_id=run_id,
                message=f"Processing stopped unexpectedly: {exc}",
            )
        mark_unfinished(
            "RECOVERY_REQUIRED",
            f"Processing stopped unexpectedly before this record was completed: {exc}",
            exclude_record_id=current_record.get("record_id", "") if current_record and current_status in SUCCESS_STATUSES else "",
        )
        run["results"].append({"row": run.get("current_row", ""), "employee_name": run.get("current_employee", ""), "status": "Failed", "error_type": type(exc).__name__, "workflow_step": "Run worker", "message": str(exc), "screenshot": "", "completed_at": now()})
        if not options.get("auth_only"):
            refresh_handover()
            notify_handover()
        STORE.event(run_id, "RUN_FAILED", "FAILED", message=str(exc), error_type=type(exc).__name__)
    finally:
        if mock_server:
            mock_server.stop()
        if browser:
            try:
                browser.close()
            except Exception:
                pass


# The frontend is the only user-facing UI. The API intentionally does not
# serve a second legacy dashboard or a demo portal.

class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def create_ui_session(account: dict[str, Any]) -> str:
    token, _ttl = create_access_token(
        account["username"],
        [account["role"]],
        account.get("company_ids", []),
        account.get("permissions", default_permissions(account["role"])),
        account.get("module_access", default_module_access(account["role"])),
    )
    return token


def authenticate_ui_user(username: str, password: str) -> dict[str, Any] | None:
    account = USER_STORE.authenticate(username, password)
    if account:
        return account
    # Environment credentials remain supported for deployment, but receive
    # the explicitly configured role and never bypass the account store.
    configured_username = os.getenv("EPFO_UI_USER", "")
    configured_password = os.getenv("EPFO_UI_PASSWORD", "")
    if configured_username and configured_password and username == configured_username and password == configured_password:
        return {
            "id": "environment-account",
            "username": username,
            "display_name": username,
            "role": os.getenv("EPFO_UI_ROLE", ROLE_ADMIN),
            "active": True,
            "company_ids": [item.strip() for item in os.getenv("EPFO_UI_COMPANIES", "*").split(",") if item.strip()],
            "permissions": default_permissions(os.getenv("EPFO_UI_ROLE", ROLE_ADMIN)),
            "module_access": default_module_access(os.getenv("EPFO_UI_ROLE", ROLE_ADMIN)),
        }
    return None


def session_identity(headers) -> dict[str, Any] | None:
    authorization = headers.get("Authorization", "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    cookie = headers.get("Cookie", "")
    if not token:
        token = next((part.strip().split("=", 1)[1] for part in cookie.split(";") if part.strip().startswith(f"{JWT_COOKIE}=")), "")
    return decode_access_token(token) if token else None


def session_username(headers) -> str:
    identity = session_identity(headers)
    return str(identity.get("sub", "")) if identity else ""


def _credential_scope_key(company_id: str, deputed_company_id: str = "") -> str:
    return f"{company_id}:{deputed_company_id or 'none'}"


def _company_config_or_empty(company_id: str) -> dict[str, Any]:
    try:
        return PLATFORM.config(company_id)
    except ValueError:
        # Source-backed companies are intentionally not required to exist in
        # the local demo company registry. Their credentials are still kept
        # safely in the selected company/deputed-company session scope.
        return {}


def credential_status(company_id: str, deputed_company_id: str = "") -> dict[str, Any]:
    config = _company_config_or_empty(company_id)
    env_names = config.get("credential_env", {})
    scope_key = _credential_scope_key(company_id, deputed_company_id)
    with CREDENTIAL_LOCK:
        memory = SESSION_CREDENTIALS.get(scope_key)
        if not memory:
            memory = CREDENTIAL_VAULT.get(scope_key) or {}
            if memory:
                SESSION_CREDENTIALS[scope_key] = memory
    env_ready = bool(os.getenv(env_names.get("username", ""), "") and os.getenv(env_names.get("password", ""), ""))
    memory_ready = bool(memory.get("username") and memory.get("password"))
    return {"company_id": company_id, "deputed_company_id": deputed_company_id, "portal_profile": config.get("portal_profile", ""), "configured": env_ready or memory_ready, "password_configured": bool(env_ready or memory_ready), "source": "environment" if env_ready else ("persistent" if memory_ready else "not_configured"), "username": memory.get("username", "") if memory_ready else "", "username_env": env_names.get("username", ""), "password_env": env_names.get("password", "")}


def save_session_credentials(company_id: str, username: str, password: str, deputed_company_id: str = "") -> dict[str, Any]:
    if not username or not password:
        raise ValueError("Both PF employer username and password are required.")
    scope_key = _credential_scope_key(company_id, deputed_company_id)
    with CREDENTIAL_LOCK:
        CREDENTIAL_VAULT.set(scope_key, username, password)
        SESSION_CREDENTIALS[scope_key] = {"username": username, "password": password}
    return credential_status(company_id, deputed_company_id)


def resolve_credentials(company_id: str, username: str = "", password: str = "", deputed_company_id: str = "") -> tuple[str, str]:
    if username and password:
        return username, password
    config = _company_config_or_empty(company_id)
    env_names = config.get("credential_env", {})
    scope_key = _credential_scope_key(company_id, deputed_company_id)
    with CREDENTIAL_LOCK:
        memory = SESSION_CREDENTIALS.get(scope_key)
        if not memory:
            memory = CREDENTIAL_VAULT.get(scope_key) or {}
            if memory:
                SESSION_CREDENTIALS[scope_key] = memory
    return (memory.get("username") or os.getenv(env_names.get("username", ""), ""), memory.get("password") or os.getenv(env_names.get("password", ""), ""))


def auto_process_company(company_id: str, deputed_company_id: str = "") -> dict[str, Any]:
    """Start one isolated REAL run for newly ingested records in a company inbox."""
    if not AUTO_PROCESS_ENABLED:
        return {"enabled": False, "status": "DISABLED", "message": "Automatic processing is disabled by configuration."}
    scope_key = f"{company_id}:{deputed_company_id or 'none'}"
    with AUTO_PROCESS_LOCKS_GUARD:
        lock = AUTO_PROCESS_LOCKS.setdefault(scope_key, threading.Lock())
    with lock:
        with STORE.lock:
            active = [
                item for item in STORE.runs.values()
                if item.get("options", {}).get("company_id") == company_id
                and item.get("options", {}).get("deputed_company_id", "") == deputed_company_id
                and item.get("options", {}).get("auto_process")
                and item.get("status") not in TERMINAL_STATUSES
            ]
        if active:
            return {"enabled": True, "status": "ALREADY_RUNNING", "run_id": active[0]["run_id"], "message": "A company-scoped automatic run is already active."}
        records = [
            item for item in PLATFORM.records(company_id, deputed_company_id=deputed_company_id)
            if item.get("status") in {"NEW", "RETRY_QUEUED"}
        ]
        if not records:
            return {"enabled": True, "status": "NO_ELIGIBLE_RECORDS", "message": "No new or explicitly retried records are waiting for automatic processing."}
        checked = PLATFORM.validate(company_id, [item["record_id"] for item in records], deputed_company_id)
        valid_ids = set(checked.get("valid_record_ids", []))
        for row in checked.get("rows", []):
            if not row.get("valid"):
                PLATFORM.update_status(company_id, row["record_id"], "VALIDATION_FAILED", message=row.get("message", "Automatic validation failed."))
        valid_records = [item for item in records if item.get("record_id") in valid_ids]
        if not valid_records:
            return {"enabled": True, "status": "VALIDATION_FAILED", "total": len(records), "message": "New records were rejected by validation."}
        username, password = resolve_credentials(company_id, deputed_company_id=deputed_company_id)
        if not username or not password:
            return {"enabled": True, "status": "WAITING_FOR_CONFIGURATION", "total": len(valid_records), "message": "Valid records are waiting for company-specific portal credentials."}
        first = valid_records[0]
        options = {
            "mode": "REAL",
            "company_id": company_id,
            "company_name": first.get("company", ""),
            "deputed_company_id": deputed_company_id,
            "deputed_company_name": first.get("deputed_company", ""),
            "record_ids": [item["record_id"] for item in valid_records],
            "source": first.get("source", ""),
            "confirm_real": True,
            "headless": False,
            "auto_process": True,
        }
        run = STORE.create(valid_records, options)
        for item in valid_records:
            PLATFORM.update_status(company_id, item["record_id"], "AUTO_QUEUED", run_id=run["run_id"], message="Queued for automatic processing.")
        threading.Thread(target=run_worker, args=(run["run_id"], username, password), daemon=True, name=f"auto-process-{company_id}").start()
        return {"enabled": True, "status": "STARTED", "run_id": run["run_id"], "total": len(valid_records), "message": "Automatic processing started for validated records."}


def _period_bounds(period: str) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    normalized = period.lower()
    if normalized == "daily":
        delta = 1
    elif normalized == "weekly":
        delta = 7
    elif normalized == "monthly":
        delta = 30
    elif normalized == "quarterly":
        delta = 90
    elif normalized == "annual":
        delta = 365
    else:
        raise ValueError("period must be daily, weekly, monthly, quarterly, or annual.")
    return end.replace(microsecond=0) - timedelta(days=delta), end


def _event_in_period(value: Any, start: datetime, end: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return start <= parsed <= end
    except (TypeError, ValueError):
        return False


def log_admin_event(event: str, *, actor: str, target: str = "", details: dict[str, Any] | None = None) -> None:
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": now(), "event": event, "actor": actor, "target": target, "details": details or {}}
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        origin = self.headers.get("Origin", "")
        # The local launcher may select any free port in 5173-5190. Keep the
        # API local-only while allowing the active Vite instance to call it.
        local_origin = re.fullmatch(r"https?://(?:127\.0\.0\.1|localhost):\d{1,5}", origin)
        if local_origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def _json(self, value: Any, status: int = 200, cookie: str = "", clear_auth: bool = False):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if cookie:
            self.send_header("Set-Cookie", f"{JWT_COOKIE}={cookie}; HttpOnly; SameSite=Strict; Path=/")
        if clear_auth:
            self.send_header("Set-Cookie", f"{JWT_COOKIE}=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        key = os.getenv("EPFO_API_KEY", "")
        if key and self.headers.get("X-API-Key", "") == key:
            return True
        return bool(session_identity(self.headers))

    def _identity(self) -> dict[str, Any] | None:
        return session_identity(self.headers)

    def _api_key_authorized(self) -> bool:
        key = os.getenv("EPFO_API_KEY", "")
        return bool(key and self.headers.get("X-API-Key", "") == key)

    def _has_role(self, *roles: str) -> bool:
        if self._api_key_authorized():
            return ROLE_MASTER_ADMIN in roles or ROLE_SUPER_ADMIN in roles
        identity = self._identity()
        return bool(identity and set(identity.get("roles", [])) & set(roles))

    def _can_access_company(self, company_id: str) -> bool:
        if self._api_key_authorized():
            return True
        identity = self._identity()
        return bool(identity and USER_STORE.can_access_company(identity, str(company_id)))

    def _require_access(
        self,
        *,
        roles: tuple[str, ...] = (),
        permissions: tuple[str, ...] = (),
        company_id: str = "",
    ) -> bool:
        if not self._authorized():
            self._json(error_response("UNAUTHORIZED", "A valid JWT bearer token or configured API key is required."), 401)
            return False
        if roles and not self._has_role(*roles):
            self._json(error_response("FORBIDDEN", "Your role is not allowed to perform this action."), 403)
            return False
        if permissions and not self._api_key_authorized():
            identity = self._identity() or {}
            if not all(USER_STORE.has_permission(identity, item) for item in permissions):
                self._json(error_response("PERMISSION_DENIED", "Your account does not have permission for this action."), 403)
                return False
        if company_id and not self._can_access_company(company_id):
            self._json(error_response("COMPANY_ACCESS_DENIED", "This company is not assigned to your account."), 403)
            return False
        return True

    def _require_module(self, module: str) -> bool:
        """Enforce the module grant carried by the authenticated account."""
        if self._api_key_authorized():
            return True
        identity = self._identity() or {}
        if USER_STORE.has_module_access(identity, module):
            return True
        self._json(
            error_response(
                "MODULE_ACCESS_DENIED",
                f"Your account is not assigned access to the {str(module).upper()} module.",
            ),
            403,
        )
        return False

    def _require_intake_module(self, company_id: str, deputed_company_id: str, module: str) -> bool:
        try:
            settings = get_intake_settings(company_id, deputed_company_id)
        except ValueError as exc:
            self._json(error_response("VALIDATION_ERROR", str(exc)), 400)
            return False
        key = f"{module}_enabled"
        if settings.get(key, True):
            return True
        label = "API intake" if module == "api" else "Excel upload"
        self._json(
            error_response(
                "INTAKE_MODULE_DISABLED",
                f"{label} is disabled for the selected Company and Deputed Company scope.",
            ),
            403,
        )
        return False

    def _login_credentials(self) -> tuple[str, str]:
        # Local-only fallback keeps the desktop app usable when launched from
        # Explorer/VS Code without a shell environment. Deployment must set
        # EPFO_UI_USER and EPFO_UI_PASSWORD explicitly.
        username = os.getenv("EPFO_UI_USER", "A")
        password = os.getenv("EPFO_UI_PASSWORD", "B")
        if not username or not password:
            raise RuntimeError("EPFO_UI_USER and EPFO_UI_PASSWORD must be configured; no built-in dashboard credentials exist.")
        return username, password

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError(f"Request body exceeds the {MAX_BODY_BYTES} byte local limit.")
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ValueError("Content-Type must be application/json.")
        raw = self.rfile.read(length).decode("utf-8")
        value = json.loads(raw or "{}")
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def _multipart_run_body(self) -> dict[str, Any]:
        """Convert a workbook upload into the same normalized run payload as JSON."""
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError(f"Upload must be between 1 byte and {MAX_BODY_BYTES} bytes.")
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise ValueError("Upload Content-Type must be multipart/form-data.")
        message = BytesParser(policy=email_policy).parsebytes(
            (f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode() + self.rfile.read(length)
        )
        workbook = None
        fields: dict[str, str] = {}
        for part in message.iter_attachments():
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            if filename and name in {"file", "workbook"}:
                workbook = (filename, part.get_payload(decode=True) or b"")
            elif name:
                fields[name] = (part.get_content() or "").strip()
        if not workbook:
            raise ValueError("Upload must include a workbook field named 'file'.")
        filename, data = workbook
        if not filename.lower().endswith((".xlsx", ".xlsm")):
            raise ValueError("Only .xlsx and .xlsm workbooks are accepted.")
        upload_dir = RUN_ROOT / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / f"{uuid.uuid4().hex}_{Path(filename).name}"
        target.write_bytes(data)
        records = read_records(target)
        payload: dict[str, Any] = {"records": records, "mode": fields.get("mode", "REAL"), "filename": filename, "sha256": hashlib.sha256(data).hexdigest()}
        for key in ("username", "password", "company_id", "deputed_company_id", "scenario", "headless", "typing_delay_ms", "demo_delay_ms"):
            if key in fields:
                payload[key] = fields[key]
        if fields.get("confirm_real", "").lower() in {"1", "true", "yes"}:
            payload["confirm_real"] = True
        return payload

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)
        if path in {"/docs", "/swagger", "/swagger/index.html"}:
            payload = swagger_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        if path == "/openapi.json":
            return self._json(build_openapi(int(os.getenv("EPFO_API_PORT", "8792"))))
        if path == "":
            return self._json({
                "service": "epfo-api-local",
                "message": "API-only service. Open the frontend at http://127.0.0.1:5201/.",
                "frontend_url": "http://127.0.0.1:5201/",
            }, 404)
        if path == "/api/v1/auth/me":
            identity = self._identity()
            return self._json({"authenticated": bool(identity), "username": identity.get("sub", "") if identity else "", "display_name": identity.get("display_name", "") if identity else "", "roles": identity.get("roles", []) if identity else [], "company_ids": identity.get("company_ids", []) if identity else [], "permissions": identity.get("permissions", []) if identity else [], "module_access": identity.get("module_access", []) if identity else []})
        if path == "/health":
            config_report = validate_configuration()
            return self._json({"status": "ok" if config_report["valid"] else "degraded", "service": "epfo-api-local", "local_only": True, "storage_backend": "filesystem", "external_blob_storage": False, "configuration_valid": config_report["valid"], "configuration_errors": len(config_report["errors"])})
        if not self._authorized():
            return self._json(error_response("UNAUTHORIZED", "A valid JWT bearer token or configured API key is required."), 401)
        if path == "/api/v1/esi/template":
            if not self._require_module("ESI"):
                return
            if not self._require_access(permissions=("employee_queue.view",)):
                return
            try:
                from io import BytesIO
                from openpyxl import Workbook

                workbook = Workbook()
                sheet = workbook.active
                sheet.title = "ESI mandatory fields"
                sheet.append([FIELD_LABELS[field] for field in ESI_FIELDS])
                sheet.freeze_panes = "A2"
                sheet.auto_filter.ref = sheet.dimensions
                buffer = BytesIO()
                workbook.save(buffer)
                payload = buffer.getvalue()
            except Exception as exc:
                LOGGER.exception("Could not build ESI template")
                return self._json(error_response("TEMPLATE_ERROR", "The ESI template could not be generated.", details=str(exc)), 500)
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="ESI_registration_template.xlsx"')
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        if path == "/api/v1/esi/contract":
            if not self._require_module("ESI"):
                return
            if not self._require_access(permissions=("employee_queue.view",)):
                return
            return self._json(esi_contract())
        if path == "/api/v1/esi/inbox":
            if not self._require_module("ESI"):
                return
            company_id = query.get("company", [""])[0].strip()
            if not company_id:
                return self._json(error_response("VALIDATION_ERROR", "company query parameter is required."), 400)
            if not self._require_access(permissions=("employee_queue.view",), company_id=company_id):
                return
            deputed_company_id = query.get("deputed_company_id", [""])[0].strip()
            records = ESI_STORE.list_records(
                company_id,
                deputed_company_id=deputed_company_id,
                status=query.get("status", [""])[0].strip(),
            )
            return self._json({"module": "ESI", "company_id": company_id, "deputed_company_id": deputed_company_id, "records": records})
        if path == "/api/v1/esi/audit":
            if not self._require_module("ESI"):
                return
            company_id = query.get("company", [""])[0].strip()
            if not company_id:
                return self._json(error_response("VALIDATION_ERROR", "company query parameter is required."), 400)
            if not self._require_access(permissions=("employee_queue.view",), company_id=company_id):
                return
            deputed_company_id = query.get("deputed_company_id", [""])[0].strip()
            events = ESI_STORE.audit_events(
                company_id,
                deputed_company_id=deputed_company_id,
                limit=_safe_int_query(query.get("limit", ["200"])[0], 200),
            )
            return self._json({"module": "ESI", "company_id": company_id, "deputed_company_id": deputed_company_id, "events": events})
        if path == "/api/v1/esi/automation/status":
            if not self._require_module("ESI"):
                return
            if not self._require_access(permissions=("employee_queue.view",)):
                return
            return self._json(ESI_AUTOMATION.status())
        if path == "/api/v1/esi/report":
            if not self._require_module("ESI"):
                return
            company_id = query.get("company", [""])[0].strip()
            deputed_company_id = query.get("deputed_company_id", [""])[0].strip()
            if not company_id or not deputed_company_id:
                return self._json(error_response("VALIDATION_ERROR", "company and deputed_company_id query parameters are required."), 400)
            if not self._require_access(permissions=("reports.download",), company_id=company_id):
                return
            rows = ESI_STORE.records(company_id, deputed_company_id=deputed_company_id)
            events = ESI_STORE.audit_events(company_id, deputed_company_id=deputed_company_id, limit=1000)
            report = build_esi_report(
                rows,
                events,
                company_id=company_id,
                deputed_company_id=deputed_company_id,
                company_name=query.get("company_name", [""])[0].strip(),
                deputed_company_name=query.get("deputed_company_name", [""])[0].strip(),
            )
            fmt = query.get("format", ["json"])[0].strip().lower()
            if fmt not in {"json", "xlsx"}:
                return self._json(error_response("INVALID_FORMAT", "format must be json or xlsx."), 400)
            payload = esi_workbook_bytes(report) if fmt == "xlsx" else esi_json_bytes(report)
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if fmt == "xlsx" else "application/json; charset=utf-8"
            filename = f"ESI_report_{company_id}_{deputed_company_id}.{fmt}"
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        if path == "/api/v1/admin/esi/accounts":
            if not self._require_module("ESI"):
                return
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("credentials.view",)):
                return
            return self._json({"module": "ESI", "accounts": CREDENTIAL_VAULT.list_esi_account_metadata(client_id=query.get("client_id", [""])[0].strip())})
        if path == "/api/v1/admin/users":
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN), permissions=("users.view",)):
                return
            return self._json({"users": USER_STORE.list_users(), "roles": sorted(ROLES)})
        if path == "/api/v1/admin/permissions":
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN), permissions=("permissions.manage",)):
                return
            return self._json({"permissions": sorted(PERMISSIONS), "role_defaults": {role: default_permissions(role) for role in sorted(ROLES)}, "modules": ["PF", "ESI", "LWF", "PT"], "module_defaults": {role: default_module_access(role) for role in sorted(ROLES)}})
        if path == "/api/v1/admin/companies":
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("companies.view",)):
                return
            try:
                result = SOURCE_DATA.companies()
                # Super admins can see the complete source catalogue. Admins
                # are intentionally limited to the company IDs assigned to
                # their account; the same boundary is used by the picker and
                # the complete-data endpoint below.
                result["companies"] = [
                    item for item in result.get("companies", [])
                    if self._can_access_company(str(item.get("id", item.get("company_id", ""))))
                ]
                return self._json(result)
            except SourceDataError as exc:
                return self._json(error_response("SOURCE_UNAVAILABLE", str(exc)), 503)
        if path == "/api/v1/admin/activity":
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("activity.view",)):
                return
            limit = min(max(int(query.get("limit", ["200"])[0]), 1), 1000)
            company_id = query.get("company", [""])[0]
            events: list[dict[str, Any]] = []
            allowed_company_ids = None
            identity = self._identity() or {}
            if not ({ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN} & set(identity.get("roles", []))) and not self._api_key_authorized():
                allowed_company_ids = {str(item) for item in identity.get("company_ids", [])}
            if AUDIT_LOG_PATH.exists():
                for line in AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit * 2:]:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_company = str(item.get("company_id", ""))
                    if allowed_company_ids is not None and "*" not in allowed_company_ids and event_company not in allowed_company_ids:
                        continue
                    if company_id and item.get("company_id") != company_id:
                        continue
                    events.append(item)
            return self._json({"events": events[-limit:]})
        if path == "/api/v1/admin/reports":
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("reports.view",)):
                return
            period = query.get("period", ["monthly"])[0]
            start, end = _period_bounds(period)
            company_id = query.get("company", [""])[0]
            totals = {"intake_events": 0, "accepted_records": 0, "rejected_records": 0, "successful_runs": 0, "failed_runs": 0, "audit_events": 0}
            intake_events: list[dict[str, Any]] = []
            identity = self._identity() or {}
            allowed_company_ids = None
            if not ({ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN} & set(identity.get("roles", []))) and not self._api_key_authorized():
                allowed_company_ids = {str(item) for item in identity.get("company_ids", [])}
            if company_id and not self._can_access_company(company_id):
                return self._json(error_response("COMPANY_ACCESS_DENIED", "This company is not assigned to your account."), 403)
            if UPLOAD_LOG_PATH.exists():
                for line in UPLOAD_LOG_PATH.read_text(encoding="utf-8").splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_company = str(item.get("company_id", ""))
                    if allowed_company_ids is not None and "*" not in allowed_company_ids and event_company not in allowed_company_ids:
                        continue
                    if company_id and item.get("company_id") != company_id:
                        continue
                    if _event_in_period(item.get("timestamp"), start, end):
                        intake_events.append(item)
                        totals["intake_events"] += 1
                        totals["accepted_records"] += int(item.get("valid", 0) or 0) if item.get("status") == "ACCEPTED" else 0
                        totals["rejected_records"] += int(item.get("invalid", 0) or 0) if item.get("status") == "REJECTED" else 0
            audit_events: list[dict[str, Any]] = []
            if AUDIT_LOG_PATH.exists():
                for line in AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_company = str(item.get("company_id", ""))
                    if allowed_company_ids is not None and "*" not in allowed_company_ids and event_company not in allowed_company_ids:
                        continue
                    if company_id and item.get("company_id") != company_id:
                        continue
                    if _event_in_period(item.get("timestamp"), start, end):
                        audit_events.append(item)
            totals["audit_events"] = len(audit_events)
            with STORE.lock:
                runs = list(STORE.runs.values())
            for run in runs:
                run_company_id = str(run.get("options", {}).get("company_id", ""))
                if allowed_company_ids is not None and "*" not in allowed_company_ids and run_company_id not in allowed_company_ids:
                    continue
                if company_id and run_company_id != company_id:
                    continue
                if _event_in_period(run.get("created_at"), start, end):
                    if run.get("status") in {"COMPLETED", "COMPLETED_WITH_ERRORS"}:
                        totals["successful_runs"] += 1
                    elif run.get("status") in {"FAILED", "STOPPED", "HANDOVER_REQUIRED", "RECOVERY_REQUIRED"}:
                        totals["failed_runs"] += 1
            return self._json({"period": period, "from": start.isoformat(), "to": end.isoformat(), "totals": totals, "intake_events": intake_events[-100:], "recent_activity": audit_events[-100:]})
        if path == "/api/v1/source/status":
            return self._json(SOURCE_DATA.status())
        if path == "/api/v1/source/companies":
            if not self._require_access(permissions=("companies.view",)):
                return
            try:
                result = SOURCE_DATA.companies()
                identity = self._identity() or {}
                allowed = {str(item) for item in identity.get("company_ids", [])}
                if ROLE_MASTER_ADMIN in identity.get("roles", []) or ROLE_SUPER_ADMIN in identity.get("roles", []) or "*" in allowed:
                    return self._json(result)
                result["companies"] = [item for item in result.get("companies", []) if str(item.get("id", item.get("company_id", ""))) in allowed]
                return self._json(result)
            except SourceDataError as exc:
                source_status = SOURCE_DATA.status()
                if not source_status["database_configured"] and not source_status["api_configured"]:
                    companies = [item for item in PLATFORM.company_list() if self._can_access_company(str(item["id"]))]
                    return self._json({"companies": companies, "source": "local_company_config", "read_only": True, "warnings": [str(exc)]})
                return self._json(error_response("SOURCE_UNAVAILABLE", str(exc)), 503)
        if path.startswith("/api/v1/source/companies/") and path.endswith("/deputed"):
            raw_company_id = unquote(path.split("/")[5])
            source_status = SOURCE_DATA.status()
            if not source_status["database_configured"] and not source_status["api_configured"] and raw_company_id in PLATFORM.companies:
                if not self._require_access(permissions=("companies.view",), company_id=raw_company_id):
                    return
                company = PLATFORM.config(raw_company_id)
                return self._json({"deputed_companies": [{"id": raw_company_id, "name": company.get("deputed_company_name") or company["name"]}], "source": "local_company_config", "read_only": True, "warnings": []})
            company_id = _as_source_id(raw_company_id)
            if company_id is None:
                return self._json(error_response("INVALID_COMPANY_ID", "company_id must be numeric."), 400)
            if not self._require_access(permissions=("companies.view",), company_id=str(company_id)):
                return
            if not self._can_access_company(str(company_id)):
                return self._json(error_response("COMPANY_ACCESS_DENIED", "This company is not assigned to your account."), 403)
            try:
                return self._json(SOURCE_DATA.deputed_companies(company_id))
            except SourceDataError as exc:
                return self._json(error_response("SOURCE_UNAVAILABLE", str(exc)), 503)
        if path.startswith("/api/v1/source/companies/") and path.endswith("/complete-data"):
            if not self._require_module("PF"):
                return
            company_id = _as_source_id(path.split("/")[5])
            if company_id is None:
                return self._json(error_response("INVALID_COMPANY_ID", "company_id must be numeric."), 400)
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("intake.api",), company_id=str(company_id)):
                return
            if not self._can_access_company(str(company_id)):
                return self._json(error_response("COMPANY_ACCESS_DENIED", "This company is not assigned to your account."), 403)
            raw_deputed_id = query.get("deputed_company_id", [""])[0]
            deputed_id = _as_optional_scope_id(raw_deputed_id)
            if text(raw_deputed_id) and deputed_id is None:
                return self._json(error_response("INVALID_DEPUTED_COMPANY_ID", "deputed_company_id must be a non-negative integer."), 400)
            if text(raw_deputed_id) and not self._require_intake_module(str(company_id), text(raw_deputed_id), "api"):
                return
            try:
                updated_since = _as_source_datetime(query.get("updated_since", [""])[0], "updated_since")
            except ValueError as exc:
                return self._json(error_response("INVALID_UPDATED_SINCE", str(exc)), 400)
            try:
                joined_since = _as_source_datetime(query.get("joined_since", [""])[0], "joined_since")
            except ValueError as exc:
                return self._json(error_response("INVALID_JOINED_SINCE", str(exc)), 400)
            try:
                result = SOURCE_DATA.complete(
                    company_id,
                    deputed_id,
                    query.get("employee_code", [""])[0].strip(),
                    _safe_int_query(query.get("limit", ["500"])[0], 500),
                    _safe_int_query(query.get("offset", ["0"])[0], 0),
                    updated_since,
                    joined_since,
                )
                return self._json(result)
            except SourceDataError as exc:
                return self._json(error_response("SOURCE_UNAVAILABLE", str(exc)), 503)
        if path in {"/api/v1/template", "/api/v1/template/full"}:
            if not self._require_module("PF"):
                return
            template = PF_ROOT / "samples" / "workbooks" / "PF_upload_template.xlsx"
            filename = "PF_upload_template.xlsx"
            data = template.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f"attachment; filename={filename}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/v1/template/mandatory":
            if not self._require_module("PF"):
                return
            template = PF_ROOT / "samples" / "workbooks" / "PF_mandatory_template.xlsx"
            filename = "PF_mandatory_template.xlsx"
            data = template.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f"attachment; filename={filename}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/v1/uploads/logs":
            if not self._require_access(permissions=("employee_queue.view",)):
                return
            requested_company = query.get("company", [""])[0].strip()
            if requested_company and not self._can_access_company(requested_company):
                self._json(error_response("COMPANY_ACCESS_DENIED", "This company is not assigned to your account."), 403)
                return
            identity = self._identity()
            unrestricted = bool(self._api_key_authorized() or (identity and ({ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN} & set(identity.get("roles", [])))))
            assigned_companies = set(str(item) for item in (identity or {}).get("company_ids", []))
            events = []
            if UPLOAD_LOG_PATH.exists():
                for line in UPLOAD_LOG_PATH.read_text(encoding="utf-8").splitlines()[-200:]:
                    try:
                        item = json.loads(line)
                        event_company = str(item.get("company_id", ""))
                        if not unrestricted and "*" not in assigned_companies and event_company not in assigned_companies:
                            continue
                        if requested_company and event_company != requested_company:
                            continue
                        if query.get("deputed_company_id", [""])[0] and item.get("deputed_company_id") != query["deputed_company_id"][0]:
                            continue
                        events.append(item)
                    except json.JSONDecodeError:
                        continue
            return self._json({"events": list(reversed(events))})
        if path == "/api/v1/evidence":
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("evidence.view",)):
                return
            company = query.get("company", [""])[0]
            deputed = query.get("deputed", [""])[0]
            roots = [ROOT / "evidence", ROOT / "reports" / "qa" / "evidence"]
            files = []
            for root in roots:
              if not root.exists():
                continue
              for file in root.rglob("*.png"):
                relative = file.relative_to(root).parts
                if company and (not relative or relative[0] != company):
                    continue
                if deputed and (len(relative) < 2 or relative[1] != deputed):
                    continue
                files.append({"name": file.name, "company": relative[0] if relative else "", "deputed_company": relative[1] if len(relative) > 1 else "", "stage": relative[2] if len(relative) > 2 else "", "size": file.stat().st_size, "modified": datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).isoformat(), "path": str(file), "url": "/api/v1/evidence/file?path=" + quote(str(file.relative_to(ROOT)), safe="")})
            return self._json({"files": sorted(files, key=lambda item: item["modified"], reverse=True)})
        if path == "/api/v1/evidence/file":
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("evidence.view",)):
                return
            relative = unquote(query.get("path", [""])[0])
            target = (ROOT / relative).resolve()
            allowed_roots = [(ROOT / "evidence").resolve(), (ROOT / "reports" / "qa" / "evidence").resolve()]
            if not any(target == allowed or allowed in target.parents for allowed in allowed_roots):
                return self._json({"error": "Invalid evidence path"}, 403)
            if not target.exists() or not target.is_file() or target.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                return self._json({"error": "Evidence file not found"}, 404)
            payload = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png" if target.suffix.lower() == ".png" else "image/jpeg")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if path == "/api/v1/companies":
            if not self._require_access():
                return
            companies = []
            for company in PLATFORM.company_list():
                if not self._can_access_company(str(company["id"])):
                    continue
                item = dict(company)
                item["credential"] = credential_status(company["id"])
                companies.append(item)
            return self._json({"companies": companies})
        if path.startswith("/api/v1/companies/") and path.endswith("/schema"):
            company_id = path.split("/")[4]
            if not self._require_access(company_id=company_id):
                return
            config = PLATFORM.config(company_id)
            return self._json({"company_id": company_id, "name": config["name"], "deputed_company_name": config.get("deputed_company_name", ""), "selected_fields": config.get("selected_fields", []), "required_fields": config.get("required_fields", []), "aliases": config.get("aliases", {})})
        if path.startswith("/api/v1/companies/") and path.endswith("/credentials/status"):
            query = parse_qs(urlparse(self.path).query)
            company_id = path.split("/")[4]
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("credentials.view",), company_id=company_id):
                return
            deputed_company_id = query.get("deputed_company_id", [""])[0]
            return self._json(credential_status(company_id, deputed_company_id))
        if path.startswith("/api/v1/companies/") and path.endswith("/intake-settings"):
            company_id = path.split("/")[4]
            deputed_company_id = query.get("deputed_company_id", [""])[0].strip()
            if not self._require_access(permissions=("intake.settings.view",), company_id=company_id):
                return
            if not deputed_company_id:
                return self._json(error_response("VALIDATION_ERROR", "deputed_company_id is required."), 400)
            return self._json(get_intake_settings(company_id, deputed_company_id))
        if path == "/api/v1/admin/intake-settings":
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("intake.settings.view",)):
                return
            identity = self._identity() or {}
            allowed = None
            if not ({ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN} & set(identity.get("roles", []))) and not self._api_key_authorized():
                allowed = {str(item) for item in identity.get("company_ids", [])}
            requested_company = query.get("company_id", [""])[0].strip()
            requested_deputed = query.get("deputed_company_id", [""])[0].strip()
            if requested_company and not self._can_access_company(requested_company):
                return self._json(error_response("COMPANY_ACCESS_DENIED", "This company is not assigned to your account."), 403)
            settings = list_intake_settings(allowed)
            if requested_company:
                settings = [item for item in settings if item["company_id"] == requested_company]
            if requested_deputed:
                settings = [item for item in settings if item["deputed_company_id"] == requested_deputed]
            return self._json({"settings": settings, "storage": "sqlite"})
        if path == "/api/v1/schedules":
            if not self._authorized():
                return self._json({"error": "Authentication required"}, 401)
            return self._json({"schedules": SCHEDULER.list()})
        if path == "/api/v1/inbox":
            if not self._require_module("PF"):
                return
            query = parse_qs(urlparse(self.path).query)
            company_id = query.get("company", [""])[0]
            if not company_id:
                return self._json({"error": "company query parameter is required"}, 400)
            if not self._require_access(permissions=("employee_queue.view",), company_id=company_id):
                return
            deputed_company_id = query.get("deputed_company_id", [""])[0].strip()
            records = PLATFORM.list_records(company_id, query.get("status", [""])[0], deputed_company_id)
            return self._json({"company_id": company_id, "deputed_company_id": deputed_company_id, "records": records})
        if path == "/api/v1/inbox/audit":
            if not self._require_module("PF"):
                return
            query = parse_qs(urlparse(self.path).query)
            company_id = query.get("company", [""])[0]
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("activity.view",), company_id=company_id):
                return
            limit = min(max(int(query.get("limit", ["200"])[0]), 1), 1000)
            events: list[dict[str, Any]] = []
            if AUDIT_LOG_PATH.exists():
                for line in AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit:]:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not company_id or event.get("company_id") == company_id:
                        events.append(event)
            return self._json({"company_id": company_id, "events": events[-limit:]})
        if path == "/api/v1/schema":
            if not self._require_module("PF"):
                return
            return self._json({"fields": FIELDS, "required": PF_REQUIRED_FIELDS, "optional": [field for field in FIELDS if field not in PF_REQUIRED_FIELDS], "modes": ["REAL"], "approval": "never automated"})
        if path == "/api/v1/config/validate":
            return self._json(validate_configuration())
        if path == "/api/v1/errors":
            if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("monitoring.view",)):
                return
            query = parse_qs(urlparse(self.path).query)
            filter_run = query.get("run_id", [""])[0]
            filter_company = query.get("company", [""])[0]
            filter_category = query.get("category", [""])[0]
            items = []
            with STORE.lock:
                runs = list(STORE.runs.values())
            for item in runs:
                if filter_run and item.get("run_id") != filter_run:
                    continue
                if filter_company and item.get("options", {}).get("company_id") != filter_company:
                    continue
                log = load_error_log(item["run_id"], item)
                for error in log["errors"]:
                    if filter_category and error.get("category") != filter_category:
                        continue
                    error = dict(error)
                    error["run_id"] = item["run_id"]
                    error["company_id"] = item.get("options", {}).get("company_id", "")
                    error["run_status"] = item.get("status", "")
                    error["evidence_url"] = f"/api/v1/runs/{quote(item['run_id'])}/evidence"
                    items.append(error)
            items.sort(key=lambda item: item.get("recorded_at", ""), reverse=True)
            return self._json({"count": len(items), "errors": items})
        if path.startswith("/api/v1/runs/") and path.endswith("/handover"):
            run = STORE.get(path.split("/")[4])
            if run and not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("monitoring.view",), company_id=str(run.get("options", {}).get("company_id", ""))):
                return
            return self._json(run.get("handover", {"status": "NOT_REQUIRED"}) if run else {"error": "Run not found"}, 200 if run else 404)
        if path.startswith("/api/v1/runs/"):
            parts = path.split("/")
            run_id = unquote(parts[4]) if len(parts) > 4 else ""
            run = STORE.get(run_id)
            if run and not self._require_access(permissions=("employee_queue.view",), company_id=str(run.get("options", {}).get("company_id", ""))):
                return
            if run and len(parts) >= 6 and parts[5] in {"report", "errors", "evidence"}:
                required = "reports.download" if parts[5] == "report" else ("evidence.view" if parts[5] == "evidence" else "monitoring.view")
                if not self._require_access(permissions=(required,), company_id=str(run.get("options", {}).get("company_id", ""))):
                    return
            if len(parts) == 6 and parts[5] == "report":
                if not run:
                    return self._json({"error": "Run not found"}, 404)
                if run.get("status") not in TERMINAL_STATUSES:
                    return self._json({"ready": False, "report": run.get("report")}, 200)
                if not run.get("report"):
                    try:
                        run["report"] = generate_run_report(run)
                        STORE._persist(run)
                    except Exception as exc:
                        LOGGER.exception("Could not generate report for %s", run_id)
                        return self._json(error_response("REPORT_GENERATION_FAILED", str(exc)), 500)
                return self._json({"ready": True, "report": run.get("report")})
            if len(parts) == 7 and parts[5] == "report":
                if not run:
                    return self._json({"error": "Run not found"}, 404)
                if run.get("status") not in TERMINAL_STATUSES:
                    return self._json(error_response("REPORT_NOT_READY", "The processing run has not reached a terminal status yet."), 409)
                name = unquote(parts[6])
                if name not in {"processing_report.xlsx", "processing_report.json"}:
                    return self._json(error_response("INVALID_REPORT", "Only the generated XLSX and JSON reports are downloadable."), 400)
                if not run.get("report"):
                    try:
                        run["report"] = generate_run_report(run)
                        STORE._persist(run)
                    except Exception as exc:
                        LOGGER.exception("Could not generate report for %s", run_id)
                        return self._json(error_response("REPORT_GENERATION_FAILED", str(exc)), 500)
                target = RUN_ROOT / run_id / name
                if not target.exists() or not target.is_file():
                    return self._json(error_response("REPORT_NOT_FOUND", "The processing report is not available."), 404)
                payload = target.read_bytes()
                content_type = "application/json; charset=utf-8" if target.suffix == ".json" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Content-Disposition", f"attachment; filename={quote(target.name)}")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(payload)
                return
            if len(parts) == 6 and parts[5] == "errors":
                return self._json(load_error_log(run_id, run), 200 if run else 404)
            if len(parts) == 6 and parts[5] == "evidence":
                if not run:
                    return self._json({"error": "Run not found"}, 404)
                files = []
                locations = [
                    (RUN_ROOT / run_id, "logs", {"audit.jsonl", "error_log.jsonl", "errors.json"}),
                    (RUN_ROOT / run_id / "artifacts", "artifacts", None),
                    (BLOB_ROOT / run_id, "blob", None),
                ]
                for root, source, allowed in locations:
                    if not root.exists():
                        continue
                    for file in sorted(root.iterdir()):
                        if not file.is_file() or (allowed is not None and file.name not in allowed):
                            continue
                        files.append({"name": file.name, "source": source, "size": file.stat().st_size, "url": f"/api/v1/runs/{quote(run_id)}/evidence/{source}/{quote(file.name)}"})
                return self._json({"run_id": run_id, "files": files})
            if len(parts) == 8 and parts[5] == "evidence":
                if not run:
                    return self._json({"error": "Run not found"}, 404)
                source = parts[6]
                name = unquote(parts[7])
                if Path(name).name != name or source not in {"logs", "artifacts", "blob"}:
                    return self._json({"error": "Invalid evidence path"}, 400)
                roots = {"logs": RUN_ROOT / run_id, "artifacts": RUN_ROOT / run_id / "artifacts", "blob": BLOB_ROOT / run_id}
                target = roots[source] / name
                allowed_logs = {"audit.jsonl", "error_log.jsonl", "errors.json"}
                if source == "logs" and name not in allowed_logs:
                    return self._json({"error": "Evidence file is not exposed"}, 403)
                if not target.exists() or not target.is_file():
                    return self._json({"error": "Evidence file not found"}, 404)
                payload = target.read_bytes()
                content_type = "application/octet-stream"
                if target.suffix.lower() == ".json":
                    content_type = "application/json; charset=utf-8"
                elif target.suffix.lower() == ".jsonl":
                    content_type = "application/x-ndjson; charset=utf-8"
                elif target.suffix.lower() == ".png":
                    content_type = "image/png"
                elif target.suffix.lower() in {".jpg", ".jpeg"}:
                    content_type = "image/jpeg"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Content-Disposition", f"inline; filename={quote(target.name)}")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                return
        if path.startswith("/api/v1/runs/"):
            run = STORE.snapshot(path.rsplit("/", 1)[-1])
            return self._json(run or {"error": "Run not found"}, 200 if run else 404)
        return self._json({"error": "Not found"}, 404)

    def do_POST(self):
        try:
            path = urlparse(self.path).path.rstrip("/")
            if path == "/api/v1/runs/upload" or ((path.startswith("/api/v1/companies/") or path.startswith("/api/v1/esi/companies/")) and path.endswith(("/upload", "/validate-upload"))):
                body = self._multipart_run_body()
                if path == "/api/v1/runs/upload":
                    path = "/api/v1/runs"
            else:
                body = self._body()
            if path == "/api/v1/auth/login":
                username = text(body.get("username"))
                password = text(body.get("password"))
                account = authenticate_ui_user(username, password)
                if not account:
                    return self._json({"error": {"code": "AUTHENTICATION_FAILED", "message": "Invalid local UI credentials."}}, 401)
                token = create_ui_session(account)
                claims = decode_access_token(token) or {}
                return self._json({"authenticated": True, "username": username, "display_name": account.get("display_name", username), "role": account.get("role", ROLE_USER), "roles": claims.get("roles", []), "company_ids": account.get("company_ids", []), "permissions": account.get("permissions", []), "module_access": claims.get("module_access", account.get("module_access", [])), "token_type": "Bearer", "access_token": token, "expires_in": max(0, int(claims.get("exp", 0)) - int(datetime.now(timezone.utc).timestamp()))}, cookie=token)
            if not self._authorized():
                return self._json(error_response("UNAUTHORIZED", "A valid JWT bearer token or configured API key is required."), 401)
            if path == "/api/v1/auth/logout":
                return self._json({"authenticated": False}, clear_auth=True)
            if path == "/api/v1/demo":
                return self._json({"error": {"code": "DEMO_DISABLED", "message": "Sample/demo workflows are disabled. Use REAL supervised mode."}}, 410)
            if path.startswith("/api/v1/esi/companies/"):
                if not self._require_module("ESI"):
                    return
                parts = path.split("/")
                if len(parts) < 7:
                    return self._json(error_response("INVALID_ESI_PATH", "Use /api/v1/esi/companies/{company_id}/{action}."), 400)
                company_id = unquote(parts[5])
                deputed_company_id = text(body.get("deputed_company_id"))
                if path.endswith("/validate-upload"):
                    if not self._require_access(permissions=("intake.excel",), company_id=company_id):
                        return
                    if not deputed_company_id:
                        return self._json(error_response("VALIDATION_ERROR", "deputed_company_id is required."), 400)
                    if not self._require_intake_module(company_id, deputed_company_id, "excel"):
                        return
                    uploaded = body
                    scan = ESI_STORE.validate_raw(
                        company_id,
                        uploaded["records"],
                        deputed_company_id=deputed_company_id,
                    )
                    scan.update({
                        "module": "ESI",
                        "filename": uploaded["filename"],
                        "sha256": uploaded["sha256"],
                        "validated": scan["invalid"] == 0,
                        "message": "ESI workbook is valid for upload." if scan["invalid"] == 0 else "Correct every ESI mandatory-field error before uploading.",
                    })
                    log_upload_event(
                        company_id=company_id,
                        deputed_company_id=deputed_company_id,
                        source="esi_workbook_validation",
                        filename=uploaded["filename"],
                        digest=uploaded["sha256"],
                        status="VALIDATED" if scan["invalid"] == 0 else "REJECTED",
                        total=scan["total"],
                        valid=scan["valid"],
                        invalid=scan["invalid"],
                        message=scan["message"],
                    )
                    if scan["invalid"]:
                        return self._json({"error": {"code": "ESI_UPLOAD_VALIDATION_FAILED", "message": scan["message"], "details": scan}}, 422)
                    return self._json(scan)
                if path.endswith("/upload"):
                    if not self._require_access(permissions=("intake.excel",), company_id=company_id):
                        return
                    if not deputed_company_id:
                        return self._json(error_response("VALIDATION_ERROR", "deputed_company_id is required."), 400)
                    if not self._require_intake_module(company_id, deputed_company_id, "excel"):
                        return
                    uploaded = body
                    scan = ESI_STORE.validate_raw(company_id, uploaded["records"], deputed_company_id=deputed_company_id)
                    if scan["invalid"]:
                        return self._json({"error": {"code": "ESI_UPLOAD_VALIDATION_FAILED", "message": "ESI workbook rejected. Validate and correct every mandatory-field error first.", "details": scan}}, 422)
                    result = ESI_STORE.ingest(company_id, uploaded["records"], source="esi_workbook_upload", deputed_company_id=deputed_company_id)
                    log_upload_event(company_id=company_id, deputed_company_id=deputed_company_id, source="esi_workbook_upload", filename=uploaded["filename"], digest=uploaded["sha256"], status="ACCEPTED", total=scan["total"], valid=scan["valid"], invalid=scan["invalid"], message="ESI workbook validated and added to the scoped inbox.")
                    first = result.get("records", [])[0] if result.get("records") else (uploaded.get("records") or [{}])[0]
                    result.update({"module": "ESI", "deputed_company_id": deputed_company_id, "auto_process": esi_pending_automation(company_id, deputed_company_id, total=result.get("created", 0), valid=result.get("created", 0), client_id=text(first.get("client_id") or first.get("ClientId")), state=text(first.get("present_state") or first.get("PresentState") or first.get("state")), district=text(first.get("present_district") or first.get("PresentDistrict") or first.get("district")))})
                    return self._json(result, 202)
                if path.endswith("/ingest"):
                    if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("intake.api",), company_id=company_id):
                        return
                    if not deputed_company_id:
                        return self._json(error_response("VALIDATION_ERROR", "deputed_company_id is required."), 400)
                    if not self._require_intake_module(company_id, deputed_company_id, "api"):
                        return
                    raw_records = body.get("employees", body.get("records", []))
                    scan = ESI_STORE.validate_raw(company_id, raw_records, deputed_company_id=deputed_company_id)
                    if scan["invalid"]:
                        return self._json({"error": {"code": "ESI_API_VALIDATION_FAILED", "message": "ESI API payload rejected by validation.", "details": scan}}, 422)
                    result = ESI_STORE.ingest(company_id, raw_records, source="esi_api", deputed_company_id=deputed_company_id)
                    first = result.get("records", [])[0] if result.get("records") else (raw_records or [{}])[0]
                    result.update({"module": "ESI", "deputed_company_id": deputed_company_id, "auto_process": esi_pending_automation(company_id, deputed_company_id, total=result.get("created", 0), valid=result.get("created", 0), client_id=text(body.get("client_id") or first.get("client_id") or first.get("ClientId")), state=text(body.get("state") or first.get("present_state") or first.get("PresentState") or first.get("state")), district=text(body.get("district") or first.get("present_district") or first.get("PresentDistrict") or first.get("district")))})
                    return self._json(result, 202)
                if path.endswith("/sync-source"):
                    if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("intake.api",), company_id=company_id):
                        return
                    if not deputed_company_id:
                        return self._json(error_response("VALIDATION_ERROR", "deputed_company_id is required for source synchronization."), 400)
                    if not self._require_intake_module(company_id, deputed_company_id, "api"):
                        return
                    source_company_id = _as_source_id(company_id)
                    deputed_source_id = _as_optional_scope_id(deputed_company_id)
                    if source_company_id is None or deputed_source_id is None:
                        return self._json(error_response("INVALID_SCOPE_ID", "ESI source synchronization requires numeric company and deputed-company IDs."), 400)
                    company_name = text(body.get("company_name"))
                    deputed_company_name = text(body.get("deputed_company_name"))
                    try:
                        source_result = ESI_SOURCE_DATA.fetch(
                            source_company_id,
                            deputed_source_id,
                            client_id=text(body.get("client_id")) or None,
                            company_name=company_name,
                            deputed_company_name=deputed_company_name,
                            employee_code=text(body.get("employee_code")),
                            limit=_safe_int_query(str(body.get("limit", 500)), 500),
                            offset=_safe_int_query(str(body.get("offset", 0)), 0),
                        )
                    except SourceDataError as exc:
                        return self._json(error_response("SOURCE_UNAVAILABLE", str(exc)), 503)
                    raw_records = []
                    for index, item in enumerate(source_result.get("records", []), start=1):
                        raw = dict(item)
                        raw.update({
                            "row_number": index,
                            "company": company_name or text(item.get("company")),
                            "deputed_company": deputed_company_name or text(item.get("deputed_company")),
                            "company_id": company_id,
                            "deputed_company_id": deputed_company_id,
                        })
                        raw_records.append(raw)
                    scan = ESI_STORE.validate_raw(company_id, raw_records, deputed_company_id=deputed_company_id, company_name=company_name, deputed_company_name=deputed_company_name)
                    result = ESI_STORE.ingest(company_id, raw_records, source="esi_source_api", deputed_company_id=deputed_company_id, company_name=company_name, deputed_company_name=deputed_company_name, allow_invalid=True)
                    sync_status = "ACCEPTED" if scan["invalid"] == 0 else "PARTIAL"
                    log_upload_event(company_id=company_id, deputed_company_id=deputed_company_id, source="esi_source_api_sync", filename="source_api", digest="", status=sync_status, total=scan["total"], valid=scan["valid"], invalid=scan["invalid"], message="ESI source records converted, validated, and stored for the selected scope.")
                    first = source_result.get("records", [])[0] if source_result.get("records") else {}
                    state = text(body.get("state") or first.get("present_state") or first.get("PresentState") or first.get("state"))
                    district = text(body.get("district") or first.get("present_district") or first.get("PresentDistrict") or first.get("district"))
                    return self._json({"module": "ESI", "company_id": company_id, "deputed_company_id": deputed_company_id, "source": "esi_source_api", "queried_at": source_result.get("queried_at", now()), "validation": scan, "ingest": result, "auto_process": esi_pending_automation(company_id, deputed_company_id, total=result.get("created", 0), valid=scan.get("valid", 0), invalid=scan.get("invalid", 0), client_id=source_result.get("client_id", ""), state=state, district=district), "source_metadata": {"count": source_result.get("count", len(raw_records)), "warnings": source_result.get("warnings", []), "sources": source_result.get("sources", {}), "route": source_result.get("route", ""), "client_id": source_result.get("client_id", ""), "joining_window_hours": source_result.get("joining_window_hours"), "excluded_outside_window": source_result.get("excluded_outside_window", 0)}}, 202)
                if len(parts) == 9 and parts[6] == "records" and parts[8] == "retry":
                    if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("runs.start",), company_id=company_id):
                        return
                    record_id = unquote(parts[7])
                    record = ESI_STORE.retry_record(company_id, record_id, deputed_company_id=deputed_company_id, approve_update=_as_bool(body.get("approve_update"), False))
                    return self._json({"module": "ESI", "record": record, "auto_process": esi_pending_automation(company_id, deputed_company_id, total=1, valid=1)}, 202)
                return self._json(error_response("NOT_FOUND", "Unknown ESI endpoint."), 404)
            if path == "/api/v1/esi/validate":
                company_id = text(body.get("company_id"))
                if not company_id or not self._require_access(permissions=("employee_queue.view",), company_id=company_id):
                    return
                return self._json(ESI_STORE.validate(company_id, record_ids=body.get("record_ids"), deputed_company_id=text(body.get("deputed_company_id"))))
            if path == "/api/v1/admin/esi/accounts":
                if not self._require_module("ESI"):
                    return
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("credentials.manage",)):
                    return
                try:
                    metadata = CREDENTIAL_VAULT.set_esi_account(
                        text(body.get("client_id")),
                        text(body.get("state")),
                        text(body.get("district")),
                        text(body.get("username")),
                        text(body.get("password")),
                        client_name=text(body.get("client_name")),
                        active=_as_bool(body.get("active"), True),
                    )
                except ValueError as exc:
                    return self._json(error_response("VALIDATION_ERROR", str(exc)), 400)
                log_admin_event(
                    "ESI_ACCOUNT_UPDATED",
                    actor=session_username(self.headers),
                    target=metadata["account_key"],
                    details={"client_id": metadata["client_id"], "state": metadata["state"], "district": metadata["district"], "active": metadata["active"]},
                )
                return self._json({"module": "ESI", "account": metadata})
            if path.startswith("/api/v1/admin/esi/accounts/") and path.endswith("/active"):
                if not self._require_module("ESI"):
                    return
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("credentials.manage",)):
                    return
                account_key = unquote(path[len("/api/v1/admin/esi/accounts/") : -len("/active")].rstrip("/"))
                if not account_key:
                    return self._json(error_response("VALIDATION_ERROR", "account key is required."), 400)
                changed = CREDENTIAL_VAULT.set_esi_account_active(account_key, _as_bool(body.get("active"), False))
                if not changed:
                    return self._json(error_response("ACCOUNT_NOT_FOUND", "ESI account was not found."), 404)
                log_admin_event("ESI_ACCOUNT_STATUS_CHANGED", actor=session_username(self.headers), target=account_key, details={"active": _as_bool(body.get("active"), False)})
                return self._json({"module": "ESI", "account_key": account_key, "active": _as_bool(body.get("active"), False)})
            if path == "/api/v1/admin/users":
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN), permissions=("users.manage", "permissions.manage")):
                    return
                requested_role = text(body.get("role")).lower() or ROLE_USER
                identity_roles = set((self._identity() or {}).get("roles", []))
                if requested_role == ROLE_MASTER_ADMIN or (requested_role == ROLE_SUPER_ADMIN and ROLE_MASTER_ADMIN not in identity_roles):
                    return self._json(error_response("ROLE_ASSIGNMENT_DENIED", "Only the Master Admin can create or assign a Super Admin account."), 403)
                account = USER_STORE.upsert(body)
                log_admin_event("ADMIN_USER_CREATED", actor=session_username(self.headers), target=account["id"], details={"username": account["username"], "role": account["role"], "company_ids": account["company_ids"], "permissions": account["permissions"]})
                return self._json({"user": account}, 201)
            if path.startswith("/api/v1/admin/users/") and path.endswith("/active"):
                user_id = path.split("/")[5]
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN), permissions=("users.manage",)):
                    return
                account = USER_STORE.set_active(user_id, bool(body.get("active", True)))
                log_admin_event("ADMIN_USER_STATUS_CHANGED", actor=session_username(self.headers), target=account["id"], details={"active": account["active"]})
                return self._json({"user": account})
            if path.startswith("/api/v1/admin/users/") and path.endswith("/delete"):
                user_id = path.split("/")[5]
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN), permissions=("users.manage",)):
                    return
                target = USER_STORE.find(user_id)
                if target and target.get("role") == ROLE_MASTER_ADMIN and ROLE_MASTER_ADMIN not in set((self._identity() or {}).get("roles", [])):
                    return self._json(error_response("ROLE_ASSIGNMENT_DENIED", "Only the Master Admin can manage a Master Admin account."), 403)
                if user_id == (self._identity() or {}).get("sub"):
                    return self._json(error_response("SELF_DELETE_BLOCKED", "The active Super Admin cannot delete its own account."), 409)
                USER_STORE.delete(user_id)
                log_admin_event("ADMIN_USER_DELETED", actor=session_username(self.headers), target=user_id)
                return self._json({"deleted": True, "id": user_id})
            if path.startswith("/api/v1/admin/users/"):
                user_id = path.split("/")[5]
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN), permissions=("users.manage", "permissions.manage")):
                    return
                identity_roles = set((self._identity() or {}).get("roles", []))
                target = USER_STORE.find(user_id)
                requested_role = text(body.get("role")).lower() or (target or {}).get("role", ROLE_USER)
                if requested_role == ROLE_MASTER_ADMIN or (requested_role == ROLE_SUPER_ADMIN and ROLE_MASTER_ADMIN not in identity_roles):
                    return self._json(error_response("ROLE_ASSIGNMENT_DENIED", "Only the Master Admin can create or assign a Super Admin account."), 403)
                if target and target.get("role") == ROLE_MASTER_ADMIN and ROLE_MASTER_ADMIN not in identity_roles:
                    return self._json(error_response("ROLE_ASSIGNMENT_DENIED", "Only the Master Admin can manage a Master Admin account."), 403)
                account = USER_STORE.upsert(body, user_id=user_id)
                log_admin_event("ADMIN_USER_UPDATED", actor=session_username(self.headers), target=account["id"], details={"username": account["username"], "role": account["role"], "company_ids": account["company_ids"], "permissions": account["permissions"]})
                return self._json({"user": account})
            if path.startswith("/api/v1/companies/") and path.endswith("/validate-upload"):
                if not self._require_module("PF"):
                    return
                company_id = path.split("/")[4]
                if not self._require_access(permissions=("intake.excel",), company_id=company_id):
                    return
                uploaded = body
                deputed_company_id = text(uploaded.get("deputed_company_id"))
                if not self._require_intake_module(company_id, deputed_company_id, "excel"):
                    return
                scan = PLATFORM.validate_mandatory_raw(
                    company_id,
                    uploaded["records"],
                    deputed_company_id=deputed_company_id,
                )
                scan.update({
                    "filename": uploaded["filename"],
                    "sha256": uploaded["sha256"],
                    "validated": scan["invalid"] == 0,
                    "message": "Workbook is valid for upload." if scan["invalid"] == 0 else "Correct every mandatory-field error before uploading.",
                })
                log_upload_event(
                    company_id=company_id,
                    deputed_company_id=deputed_company_id,
                    source="workbook_validation",
                    filename=uploaded["filename"],
                    digest=uploaded["sha256"],
                    status="VALIDATED" if scan["invalid"] == 0 else "REJECTED",
                    total=scan["total"],
                    valid=scan["valid"],
                    invalid=scan["invalid"],
                    message=scan["message"],
                )
                if scan["invalid"]:
                    return self._json({"error": {"code": "UPLOAD_VALIDATION_FAILED", "message": scan["message"], "details": scan}}, 422)
                return self._json(scan)
            if path.startswith("/api/v1/companies/") and path.endswith("/retry"):
                if not self._require_module("PF"):
                    return
                parts = path.split("/")
                if len(parts) != 8 or parts[5] != "records":
                    return self._json(error_response("INVALID_RETRY_PATH", "Use /api/v1/companies/{company_id}/records/{record_id}/retry."), 400)
                company_id = unquote(parts[4])
                record_id = unquote(parts[6])
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("runs.start",), company_id=company_id):
                    return
                deputed_company_id = text(body.get("deputed_company_id"))
                try:
                    record = PLATFORM.retry_record(
                        company_id,
                        record_id,
                        deputed_company_id=deputed_company_id,
                        approve_update=_as_bool(body.get("approve_update"), False),
                    )
                except ValueError as exc:
                    status_code = 404 if "Record not found" in str(exc) else 409
                    error_code = "RECORD_NOT_FOUND" if status_code == 404 else "RETRY_NOT_ALLOWED"
                    return self._json(error_response(error_code, str(exc)), status_code)
                automatic = auto_process_company(company_id, deputed_company_id or text(record.get("deputed_company_id")))
                return self._json({"record": record, "auto_process": automatic}, 202)
            if path.startswith("/api/v1/companies/") and path.endswith("/upload"):
                if not self._require_module("PF"):
                    return
                company_id = path.split("/")[4]
                if not self._require_access(permissions=("intake.excel",), company_id=company_id):
                    return
                uploaded = body
                deputed_company_id = text(uploaded.get("deputed_company_id"))
                if not self._require_intake_module(company_id, deputed_company_id, "excel"):
                    return
                scan = PLATFORM.validate_mandatory_raw(
                    company_id,
                    uploaded["records"],
                    deputed_company_id=deputed_company_id,
                )
                if scan["invalid"]:
                    log_upload_event(company_id=company_id, deputed_company_id=deputed_company_id, source="workbook_upload", filename=uploaded["filename"], digest=uploaded["sha256"], status="REJECTED", total=scan["total"], valid=scan["valid"], invalid=scan["invalid"], message="Workbook rejected during pre-ingest validation.")
                    return self._json({"error": {"code": "UPLOAD_VALIDATION_FAILED", "message": "Workbook rejected. Correct every validation error before uploading again.", "details": scan}}, 422)
                result = PLATFORM.ingest(
                    company_id,
                    uploaded["records"],
                    source="workbook_upload",
                    deputed_company_id=deputed_company_id,
                )
                log_upload_event(company_id=company_id, deputed_company_id=deputed_company_id, source="workbook_upload", filename=uploaded["filename"], digest=uploaded["sha256"], status="ACCEPTED", total=scan["total"], valid=scan["valid"], invalid=scan["invalid"], message="Workbook validated and added to the company inbox.")
                result["deputed_company_id"] = deputed_company_id
                result["auto_process"] = auto_process_company(company_id, deputed_company_id)
                return self._json(result, 202)
            if path.startswith("/api/v1/companies/") and path.endswith("/ingest"):
                if not self._require_module("PF"):
                    return
                company_id = path.split("/")[4]
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("intake.api",), company_id=company_id):
                    return
                deputed_company_id = text(body.get("deputed_company_id"))
                if not self._require_intake_module(company_id, deputed_company_id, "api"):
                    return
                raw_records = body.get("employees", body.get("records", []))
                scan = PLATFORM.validate_raw(
                    company_id,
                    raw_records,
                    deputed_company_id=deputed_company_id,
                )
                if scan["invalid"]:
                    return self._json({"error": {"code": "API_VALIDATION_FAILED", "message": "API payload rejected by validation.", "details": scan}}, 422)
                result = PLATFORM.ingest(
                    company_id,
                    raw_records,
                    source="api",
                    deputed_company_id=deputed_company_id,
                )
                result["deputed_company_id"] = deputed_company_id
                result["auto_process"] = auto_process_company(company_id, deputed_company_id)
                return self._json(result, 202)
            if path.startswith("/api/v1/companies/") and path.endswith("/sync-source"):
                if not self._require_module("PF"):
                    return
                company_id = path.split("/")[4]
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("intake.api",), company_id=company_id):
                    return
                source_company_id = _as_source_id(company_id)
                if source_company_id is None:
                    return self._json(error_response("INVALID_COMPANY_ID", "Source API synchronization requires a numeric company_id."), 400)
                deputed_company_id = text(body.get("deputed_company_id"))
                if not deputed_company_id:
                    return self._json(error_response("VALIDATION_ERROR", "deputed_company_id is required for source synchronization."), 400)
                if not self._require_intake_module(company_id, deputed_company_id, "api"):
                    return
                deputed_source_id = _as_optional_scope_id(deputed_company_id)
                if deputed_source_id is None:
                    return self._json(error_response("INVALID_DEPUTED_COMPANY_ID", "deputed_company_id must be a non-negative integer."), 400)
                try:
                    source_result = SOURCE_DATA.complete(
                        source_company_id,
                        deputed_source_id,
                        text(body.get("employee_code")),
                        _safe_int_query(str(body.get("limit", 500)), 500),
                        _safe_int_query(str(body.get("offset", 0)), 0),
                        None,
                        None,
                    )
                except SourceDataError as exc:
                    return self._json(error_response("SOURCE_UNAVAILABLE", str(exc)), 503)
                raw_records: list[dict[str, Any]] = []
                company_name = text(body.get("company_name"))
                deputed_company_name = text(body.get("deputed_company_name"))
                for index, item in enumerate(source_result.get("records", []), start=1):
                    fields = dict(item.get("fields") or {})
                    raw = {
                        **fields,
                        "record_id": f"source-{item.get('employee_id') or item.get('employee_code') or index}",
                        "source_row": index,
                        "emp_code": item.get("employee_code", ""),
                        "employee_code": item.get("employee_code", ""),
                        "company": company_name,
                        "company_name": company_name,
                        "deputed_company_id": deputed_company_id,
                        "deputed_company": deputed_company_name,
                        "deputed_company_name": deputed_company_name,
                    }
                    for key, extra_value in (item.get("extra") or {}).items():
                        raw.setdefault(key, extra_value)
                    raw_records.append(raw)
                scan = PLATFORM.validate_raw(
                    company_id,
                    raw_records,
                    deputed_company_id=deputed_company_id,
                    deputed_company_name=deputed_company_name,
                )
                ingest_result = PLATFORM.ingest(
                    company_id,
                    raw_records,
                    source="source_api",
                    deputed_company_id=deputed_company_id,
                    deputed_company_name=deputed_company_name,
                )
                sync_status = "ACCEPTED" if scan["invalid"] == 0 else "PARTIAL"
                log_upload_event(
                    company_id=company_id,
                    deputed_company_id=deputed_company_id,
                    source="source_api_sync",
                    filename="source_api",
                    digest="",
                    status=sync_status,
                    total=scan["total"],
                    valid=scan["valid"],
                    invalid=scan["invalid"],
                    message="Source API records converted, validated, and stored in the scoped inbox.",
                )
                automatic = auto_process_company(company_id, deputed_company_id)
                return self._json({
                    "company_id": company_id,
                    "deputed_company_id": deputed_company_id,
                    "source": "source_api",
                    "queried_at": now(),
                    "joining_window_hours": 48,
                    "validation": scan,
                    "ingest": ingest_result,
                    "auto_process": automatic,
                    "source_metadata": {
                        "count": source_result.get("count", len(raw_records)),
                        "warnings": source_result.get("warnings", []),
                        "sources": source_result.get("sources", {}),
                    },
                }, 202)
            if path.startswith("/api/v1/companies/") and path.endswith("/credentials"):
                if not self._require_module("PF"):
                    return
                company_id = path.split("/")[4]
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("credentials.manage",), company_id=company_id):
                    return
                result = save_session_credentials(
                    company_id,
                    text(body.get("username")),
                    text(body.get("password")),
                    text(body.get("deputed_company_id")),
                )
                result["auto_process"] = auto_process_company(company_id, text(body.get("deputed_company_id")))
                return self._json(result)
            if path.startswith("/api/v1/companies/") and path.endswith("/test-signin"):
                if not self._require_module("PF"):
                    return
                company_id = unquote(path.split("/")[4])
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("runs.start",), company_id=company_id):
                    return
                deputed_company_id = text(body.get("deputed_company_id"))
                if company_id not in PLATFORM.companies or deputed_company_id != company_id:
                    return self._json(error_response("INVALID_SCOPE", "Select a configured local Company and Deputed Company scope."), 400)
                if body.get("confirm_real") is not True:
                    return self._json(error_response("CONFIRM_REAL_REQUIRED", "Real portal sign-in requires confirm_real=true."), 400)
                username, password = resolve_credentials(company_id, deputed_company_id=deputed_company_id)
                if not username or not password:
                    return self._json(error_response("CREDENTIALS_MISSING", "Save PF employer credentials for this scope first."), 400)
                company = PLATFORM.config(company_id)
                options = {"mode": "REAL", "auth_only": True, "confirm_real": True, "headless": False, "company_id": company_id, "company_name": company["name"], "deputed_company_id": deputed_company_id, "deputed_company_name": company.get("deputed_company_name", "")}
                run = STORE.create([], options)
                threading.Thread(target=run_worker, args=(run["run_id"], "", ""), daemon=True).start()
                return self._json({"run_id": run["run_id"], "status": run["status"], "total": 0}, 202)
            if path.startswith("/api/v1/companies/") and path.endswith("/intake-settings"):
                company_id = path.split("/")[4]
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("intake.settings.manage",), company_id=company_id):
                    return
                deputed_company_id = text(body.get("deputed_company_id"))
                result = save_intake_settings(
                    company_id,
                    deputed_company_id,
                    api_enabled=_as_bool(body.get("api_enabled"), True),
                    excel_enabled=_as_bool(body.get("excel_enabled"), True),
                    updated_by=session_username(self.headers),
                )
                log_admin_event(
                    "INTAKE_MODULE_SETTINGS_UPDATED",
                    actor=session_username(self.headers),
                    target=f"{company_id}:{deputed_company_id}",
                    details={"api_enabled": result["api_enabled"], "excel_enabled": result["excel_enabled"]},
                )
                return self._json(result)
            if path == "/api/v1/validate":
                if not self._require_module("PF"):
                    return
                company_id = text(body.get("company_id"))
                if not company_id:
                    raise ValueError("company_id is required.")
                if not self._require_access(permissions=("employee_queue.view",), company_id=company_id):
                    return
                return self._json(PLATFORM.validate(
                    company_id,
                    body.get("record_ids"),
                    text(body.get("deputed_company_id")),
                ))
            if path == "/api/v1/schedules":
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("runs.start",), company_id=text(body.get("company_id"))):
                    return
                return self._json(SCHEDULER.upsert(text(body.get("company_id")), int(body.get("interval_seconds", 300)), text(body.get("source_url"))), 201)
            if path.startswith("/api/v1/schedules/") and path.endswith("/delete"):
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN)):
                    return
                schedule_id = path.split("/")[4]
                return self._json({"deleted": SCHEDULER.remove(schedule_id)})
            if len(path.split("/")) == 7 and path.startswith("/api/v1/runs/") and path.endswith("/handover/acknowledge"):
                run_id = path.split("/")[4]
                run = STORE.get(run_id)
                if not run:
                    return self._json({"error": "Run not found"}, 404)
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("monitoring.view",), company_id=str(run.get("options", {}).get("company_id", ""))):
                    return
                if run.get("handover"):
                    run["handover"]["status"] = "ACKNOWLEDGED"
                    run["handover"]["acknowledged_at"] = now()
                    STORE.event(run_id, "HANDOVER_ACKNOWLEDGED", run["status"], message="Manual handover acknowledged; no automatic retry was made.")
                return self._json({"run_id": run_id, "acknowledged": True})
            if len(path.split("/")) == 7 and path.startswith("/api/v1/runs/") and path.endswith("/handover/email"):
                run_id = path.split("/")[4]
                run = STORE.get(run_id)
                if not run:
                    return self._json(error_response("NOT_FOUND", "Run not found."), 404)
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("monitoring.view",), company_id=str(run.get("options", {}).get("company_id", ""))):
                    return
                if not run.get("handover"):
                    return self._json(error_response("HANDOVER_NOT_READY", "This run has no failed-row handover."), 409)
                result = attempt_handover_email(run_id, force=True)
                return self._json({"run_id": run_id, "handover_email": result}, 200 if result.get("sent") else 503)
            if path == "/api/v1/runs":
                if not self._require_module("PF"):
                    return
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), company_id=text(body.get("company_id"))):
                    return
                allowed = (set(ALLOWED_OPTIONS) - {"auth_only"}) | {"employees", "records", "username", "password"}
                unknown = sorted(set(body) - allowed)
                if unknown:
                    raise ValueError("Unsupported run fields: " + ", ".join(unknown))
                mode = str(body.get("mode", "REAL")).upper()
                if mode not in {"REAL", "MOCK"}:
                    raise ValueError("mode must be REAL or MOCK.")
                if mode == "MOCK" and os.getenv("EPFO_ALLOW_MOCK", "").lower() not in {"1", "true", "yes", "on"}:
                    raise ValueError("MOCK mode is disabled outside the private regression harness. Use REAL mode for the original EPFO portal.")
                if mode == "REAL" and body.get("confirm_real") is not True:
                    raise ValueError("REAL mode requires confirm_real=true because it opens the real portal.")
                if body.get("company_id"):
                    company_id = text(body.get("company_id"))
                    deputed_company_id = text(body.get("deputed_company_id"))
                    records = PLATFORM.records(company_id, body.get("record_ids"), deputed_company_id)
                    if not records:
                        raise ValueError("No selected inbox records found for this Company and Deputed Company scope.")
                    max_batch = configured_max_batch_records()
                    if len(records) > max_batch:
                        raise ValueError(f"Selected {len(records)} records; the configured maximum is {max_batch}.")
                    checked = PLATFORM.validate(company_id, [record["record_id"] for record in records], deputed_company_id)
                    by_id = {record["record_id"]: record for record in records}
                    failures = []
                    for item in checked["rows"]:
                        if not item["valid"]:
                            record = by_id[item["record_id"]]
                            failures.append(result_for(record, "Validation Failed", item["message"], error_type="Input Data Validation", step="Company pre-insertion validation"))
                else:
                    records, failures = validate_payload(body)
                options = dict(body)
                options["mode"] = mode
                if body.get("company_id"):
                    options["company_id"] = company_id
                    options["deputed_company_id"] = deputed_company_id
                    first = records[0] if records else {}
                    options.setdefault("company_name", first.get("company", ""))
                    options.setdefault("deputed_company_name", first.get("deputed_company", ""))
                    options.setdefault("source", first.get("source", ""))
                run = STORE.create(records, options)
                run["results"].extend(failures)
                threading.Thread(target=run_worker, args=(run["run_id"], text(body.get("username")), text(body.get("password"))), daemon=True).start()
                return self._json({"run_id": run["run_id"], "status": run["status"], "total": run["total"], "validation_failures": len(failures)}, 202)
            parts = path.split("/")
            if len(parts) == 6 and parts[:4] == ["", "api", "v1", "runs"] and parts[5] in {"stop", "continue"}:
                run_id = parts[4]
                run = STORE.get(run_id)
                if not run:
                    return self._json({"error": "Run not found"}, 404)
                if not self._require_access(roles=(ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN), permissions=("runs.start",), company_id=str(run.get("options", {}).get("company_id", ""))):
                    return
                if parts[5] == "stop":
                    run["stop_event"].set()
                    STORE.event(run_id, "STOP_REQUESTED", run["status"], message="Stop requested; current row will finish safely.")
                else:
                    run["continue_event"].set()
                    STORE.event(run_id, "CONTINUE_REQUESTED", run["status"], message="Operator requested an authentication state check.")
                return self._json({"run_id": run_id, "accepted": True})
            return self._json({"error": "Not found"}, 404)
        except json.JSONDecodeError as exc:
            return self._json(error_response("MALFORMED_JSON", "Request body is not valid JSON.", details=str(exc)), 400)
        except ValueError as exc:
            return self._json(error_response("VALIDATION_ERROR", str(exc)), 400)
        except Exception as exc:
            LOGGER.exception("Unhandled API error")
            return self._json(error_response("INTERNAL_ERROR", "The server could not complete the request."), 500)

    def log_message(self, *_args):
        return


def main() -> None:
    host = os.getenv("EPFO_API_HOST", "127.0.0.1")
    port = int(os.getenv("EPFO_API_PORT", "8792"))
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("This service is local-only; EPFO_API_HOST must be 127.0.0.1, localhost, or ::1.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(f"EPFO API: http://{host}:{port}")
    server = LocalHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
