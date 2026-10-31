"""Company-aware local platform services used by the portal API."""

from __future__ import annotations

import json
import hashlib
import re
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from Platform.backend.data_processing.duplicate_checker import duplicate_errors
from Platform.backend.data_processing.validator import validate_record_all, validation_summary
from PF.backend.epfo_bot import ALIASES, norm


ROOT = Path(__file__).resolve().parent
COMPANY_CONFIG_PATH = ROOT / "config" / "company_configs.json"
INBOX_ROOT = ROOT / "api_inbox"
BLOB_ROOT = ROOT / "blob_storage"
AUDIT_LOG_PATH = INBOX_ROOT / "audit_events.jsonl"
STORAGE_BACKEND = "filesystem"
SUCCESS_STATUSES = {
    "SUCCESS",
    "COMPLETED",
    "COMPLETED_WITH_ERRORS",
    "Registered - Pending Approval",
}
IN_FLIGHT_STATUSES = {"AUTO_QUEUED", "PROCESSING", "AUTHENTICATING"}
RETRYABLE_STATUSES = {"FAILED", "VALIDATION_FAILED", "RECOVERY_REQUIRED"}
ATTENTION_STATUSES = {"UPDATE_REVIEW", "DUPLICATE_SKIPPED"}
DEFAULT_SELECTED_FIELDS = [
    "sl_no", "emp_code", "company", "name", "doj", "dob", "aadhaar",
    "father_name", "marital_status", "husband_wife_name", "gender", "mobile",
    "email", "location", "basic_wage", "uan",
]
DEFAULT_REQUIRED_FIELDS = ["uan", "aadhaar", "name", "dob", "doj", "basic_wage"]


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "artifact"


def value(value: Any) -> str:
    return "" if value is None else str(value).strip()


def source_value(raw: Any) -> str:
    """Preserve source whitespace for validation and auditability."""
    return "" if raw is None else str(raw)


def canonical_date(raw: Any) -> str:
    """Convert common API/Excel dates to the PF intake DD-MM-YYYY format."""
    if raw in (None, ""):
        return ""
    if isinstance(raw, datetime):
        return raw.strftime("%d-%m-%Y")
    if isinstance(raw, date):
        return raw.strftime("%d-%m-%Y")
    text = str(raw).strip()
    if not text:
        return ""
    candidate = text.split("T", 1)[0].split(" ", 1)[0]
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(candidate, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    return text


def canonical_wage(raw: Any) -> str:
    if raw in (None, ""):
        return ""
    text = str(raw).strip().replace(",", "")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    return str(int(number)) if number.is_integer() else f"{number:.2f}".rstrip("0").rstrip(".")


def _identity_text(raw: Any) -> str:
    return re.sub(r"\s+", " ", value(raw)).strip().upper()


def _digits(raw: Any) -> str:
    return re.sub(r"\D", "", value(raw))


class LocalBlobStore:
    """Blob-storage abstraction; local disk is the current deployment target."""

    backend = STORAGE_BACKEND

    def save_json(self, run_id: str, name: str, payload: Any) -> str:
        target = BLOB_ROOT / safe_name(run_id) / safe_name(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(target)
        return str(target)

    def save_bytes(self, run_id: str, name: str, payload: bytes) -> str:
        target = BLOB_ROOT / safe_name(run_id) / safe_name(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(payload)
        temp.replace(target)
        return str(target)


class CompanyPlatform:
    def __init__(self):
        self.lock = threading.RLock()
        self.blobs = LocalBlobStore()
        self.companies = self._load_companies()

    def _load_companies(self) -> dict[str, dict[str, Any]]:
        raw = json.loads(COMPANY_CONFIG_PATH.read_text(encoding="utf-8"))
        companies = raw.get("companies", [])
        loaded = {item["id"]: item for item in companies if item.get("id") and item.get("name")}
        if not loaded:
            raise ValueError("At least one company configuration is required.")
        return loaded

    def company_list(self) -> list[dict[str, Any]]:
        return [{
            "id": item["id"],
            "name": item["name"],
            "deputed_company_name": item.get("deputed_company_name", ""),
            "selected_fields": item.get("selected_fields", []),
            "source_url": item.get("source_url", ""),
            "portal_profile": item.get("portal_profile", ""),
            "selector_profile": item.get("selector_profile", "EPFO_EMPLOYER_DEFAULT"),
        } for item in self.companies.values()]

    def config(self, company_id: str) -> dict[str, Any]:
        if company_id in self.companies:
            return self.companies[company_id]
        # Source-backed companies stay in the read-only source catalogue, but
        # still use the same stable PF schema for local validation and inbox
        # isolation. This avoids mixing them with local demo configurations.
        return {
            "id": company_id,
            "name": company_id,
            "deputed_company_name": "",
            "selected_fields": list(DEFAULT_SELECTED_FIELDS),
            "required_fields": list(DEFAULT_REQUIRED_FIELDS),
            "aliases": {},
            "source_url": "",
            "portal_profile": "",
            "selector_profile": "EPFO_EMPLOYER_DEFAULT",
        }

    def _inbox_path(self, company_id: str) -> Path:
        return INBOX_ROOT / f"{safe_name(company_id)}.json"

    def _read_inbox(self, company_id: str) -> list[dict[str, Any]]:
        path = self._inbox_path(company_id)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    def _write_inbox(self, company_id: str, rows: list[dict[str, Any]]) -> None:
        path = self._inbox_path(company_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _audit(self, event: str, company_id: str, *, deputed_company_id: str = "", **details: Any) -> None:
        """Append a non-secret, UTC-timestamped intake/status audit event."""
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "event_id": str(uuid.uuid4()),
            "timestamp": timestamp(),
            "event": event,
            "company_id": company_id,
            "deputed_company_id": value(deputed_company_id),
            **details,
        }
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _dedupe_key(item: dict[str, Any]) -> tuple[str, str, str]:
        """Return a stable identity within one company/deputed-company scope.

        The source employee ID is strongest, followed by UAN and employee
        code. A deterministic fingerprint is used for incomplete rows so
        repeated invalid input cannot create an unbounded collection.
        """
        deputed_company_id = value(item.get("deputed_company_id"))
        source_employee_id = _identity_text(item.get("source_employee_id") or item.get("employee_id"))
        uan = _digits(item.get("uan"))
        emp_code = _identity_text(item.get("emp_code"))
        if source_employee_id:
            return (deputed_company_id, "source_employee_id", source_employee_id)
        if uan:
            return (deputed_company_id, "uan", uan)
        if emp_code:
            return (deputed_company_id, "emp_code", emp_code)
        fingerprint = item.get("payload_hash") or CompanyPlatform._payload_hash(item)
        return (deputed_company_id, "fingerprint", fingerprint)

    @staticmethod
    def _payload_hash(item: dict[str, Any]) -> str:
        """Hash canonical business fields, excluding volatile/source metadata."""
        fields = {
            key: value(item.get(key))
            for key in (
                "uan", "aadhaar", "name", "dob", "doj", "basic_wage",
                "emp_code", "gender", "father_name", "marital_status",
                "husband_wife_name", "mobile", "email", "location",
                "company_id", "deputed_company_id",
            )
        }
        encoded = json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _key_text(key: tuple[str, str, str]) -> str:
        return ":".join(key)

    @staticmethod
    def _history_event(
        row: dict[str, Any],
        status: str,
        *,
        event: str,
        message: str = "",
        source: str = "",
        run_id: str = "",
    ) -> None:
        row.setdefault("status_history", []).append({
            "timestamp": timestamp(),
            "status": status,
            "event": event,
            "message": message,
            "source": source or row.get("source", ""),
            "run_id": run_id,
        })

    @staticmethod
    def _public_record(row: dict[str, Any]) -> dict[str, Any]:
        public = {key: item for key, item in row.items() if key != "source_payload"}
        pending = public.get("pending_update")
        if isinstance(pending, dict):
            public["pending_update"] = {
                key: item for key, item in pending.items() if key != "source_payload"
            }
        return public

    def normalize(
        self,
        company_id: str,
        raw: dict[str, Any],
        index: int = 0,
        deputed_company_id: str = "",
        deputed_company_name: str = "",
    ) -> dict[str, Any]:
        cfg = self.config(company_id)
        by_name = {norm(key): item for key, item in raw.items()}
        record: dict[str, str] = {}
        for field in cfg.get("selected_fields", []):
            candidates = [field, *ALIASES.get(field, []), *cfg.get("aliases", {}).get(field, [])]
            raw_value = next((by_name[norm(candidate)] for candidate in candidates if norm(candidate) in by_name), "")
            record[field] = (
                canonical_date(raw_value) if field in {"dob", "doj"}
                else canonical_wage(raw_value) if field == "basic_wage"
                else source_value(raw_value)
            )
        record["company_id"] = company_id
        record["company"] = value(
            raw.get("company") or raw.get("company_name") or cfg.get("name", company_id)
        )
        record["deputed_company_id"] = value(
            deputed_company_id or raw.get("deputed_company_id") or raw.get("deputedCompanyId")
        )
        record["deputed_company"] = value(
            deputed_company_name
            or raw.get("deputed_company")
            or raw.get("deputed_company_name")
            or cfg.get("deputed_company_name", "")
        )
        record["source_employee_id"] = value(
            raw.get("source_employee_id") or raw.get("employee_id") or raw.get("EmployeeId")
        )
        record["row_number"] = value(raw.get("row_number") or raw.get("source_row") or raw.get("sl_no") or index + 1)
        record["record_id"] = value(raw.get("record_id")) or str(uuid.uuid4())
        record["source_updated_at"] = timestamp()
        record["source_payload"] = raw
        record["payload_hash"] = self._payload_hash(record)
        return record

    def ingest(
        self,
        company_id: str,
        raw_records: list[dict[str, Any]],
        source: str = "api",
        deputed_company_id: str = "",
        deputed_company_name: str = "",
    ) -> dict[str, Any]:
        if not isinstance(raw_records, list):
            raise ValueError("employees must be an array.")
        with self.lock:
            existing = self._read_inbox(company_id)
            for legacy in existing:
                legacy.setdefault("payload_hash", self._payload_hash(legacy))
                legacy.setdefault("first_seen_at", legacy.get("source_updated_at", timestamp()))
                legacy.setdefault("last_seen_at", legacy.get("source_updated_at", legacy["first_seen_at"]))
                legacy.setdefault("attempt_count", 0)
                legacy.setdefault("retry_count", 0)
                legacy.setdefault("duplicate_count", 0)
                legacy.setdefault("status_history", [])
            by_key = {self._dedupe_key(item): item for item in existing}
            created = 0
            updated = 0
            duplicate_skipped = 0
            duplicate_same_payload = 0
            duplicate_success = 0
            retryable_existing = 0
            update_review = 0
            now_seen = timestamp()
            for index, raw in enumerate(raw_records):
                if not isinstance(raw, dict):
                    raise ValueError("Every employee item must be an object.")
                item = self.normalize(company_id, raw, index, deputed_company_id, deputed_company_name)
                item["source"] = source
                key = self._dedupe_key(item)
                previous = by_key.get(key)
                if previous:
                    previous.setdefault("first_seen_at", previous.get("source_updated_at", now_seen))
                    previous["last_seen_at"] = now_seen
                    previous["last_source"] = source
                    previous["duplicate_count"] = int(previous.get("duplicate_count", 0) or 0) + 1
                same_payload = bool(previous and previous.get("payload_hash") == item.get("payload_hash"))
                if previous and same_payload:
                    duplicate_skipped += 1
                    duplicate_same_payload += 1
                    if previous.get("status") in SUCCESS_STATUSES:
                        duplicate_success += 1
                    if previous.get("status") in RETRYABLE_STATUSES:
                        retryable_existing += 1
                    self._history_event(
                        previous,
                        "DUPLICATE_SKIPPED",
                        event="DUPLICATE_SKIPPED",
                        message=(
                            "Identical payload already completed; no resubmission was made."
                            if previous.get("status") in SUCCESS_STATUSES
                            else "Identical payload already exists; explicit retry is required after a failed attempt."
                        ),
                        source=source,
                    )
                    self._audit(
                        "DUPLICATE_SKIPPED",
                        company_id,
                        deputed_company_id=item.get("deputed_company_id", ""),
                        source=source,
                        record_id=previous.get("record_id", ""),
                        employee_name=previous.get("name", ""),
                        dedupe_key=f"{key[0]}:{key[1]}:{key[2]}",
                        existing_status=previous.get("status", ""),
                        payload_hash=item.get("payload_hash", ""),
                    )
                    continue
                if previous:
                    item["record_id"] = previous["record_id"]
                    item["first_seen_at"] = previous.get("first_seen_at", now_seen)
                    item["last_seen_at"] = now_seen
                    item["attempt_count"] = previous.get("attempt_count", 0)
                    item["retry_count"] = previous.get("retry_count", 0)
                    item["status_history"] = list(previous.get("status_history", []))
                    if previous.get("status") in SUCCESS_STATUSES | IN_FLIGHT_STATUSES:
                        # Never silently mutate a processed or in-flight row.
                        # Keep the last processed payload and stage the source
                        # change for an explicit operator decision.
                        previous["pending_update"] = item
                        previous["pending_update_hash"] = item.get("payload_hash", "")
                        previous["update_detected_at"] = now_seen
                        previous["update_source"] = source
                        previous["status"] = "UPDATE_REVIEW"
                        self._history_event(
                            previous,
                            "UPDATE_REVIEW",
                            event="SOURCE_UPDATE_REVIEW",
                            message="Changed payload detected after processing; review and explicitly reprocess the update.",
                            source=source,
                        )
                        update_review += 1
                        by_key[key] = previous
                        self._audit(
                            "SOURCE_UPDATE_REVIEW",
                            company_id,
                            deputed_company_id=item.get("deputed_company_id", ""),
                            source=source,
                            record_id=previous.get("record_id", ""),
                            employee_name=previous.get("name", ""),
                            dedupe_key=self._key_text(key),
                            previous_status=previous.get("status", ""),
                        )
                        continue
                    # A changed failed/validation-failed row is a new source
                    # version. It is eligible for validation, but is not
                    # submitted until the normal queue gate accepts it.
                    item["status"] = "NEW"
                    item["last_failure_at"] = previous.get("last_failure_at", "")
                    self._history_event(
                        item,
                        "NEW",
                        event="SOURCE_UPDATE_ACCEPTED",
                        message="Changed source payload replaced a failed version and is ready for validation.",
                        source=source,
                    )
                    by_key[key] = item
                    updated += 1
                else:
                    item["status"] = "NEW"
                    item["first_seen_at"] = now_seen
                    item["last_seen_at"] = now_seen
                    item["last_source"] = source
                    item["attempt_count"] = 0
                    item["retry_count"] = 0
                    item["duplicate_count"] = 0
                    self._history_event(
                        item,
                        "NEW",
                        event="RECORD_CREATED",
                        message="Record accepted into the scoped inbox and awaiting validation.",
                        source=source,
                    )
                    by_key[key] = item
                    created += 1
                self._audit(
                    "RECORD_INGESTED" if not previous else "RECORD_UPDATED",
                    company_id,
                    deputed_company_id=item.get("deputed_company_id", ""),
                    source=source,
                    record_id=item.get("record_id", ""),
                    employee_name=item.get("name", ""),
                    status=item.get("status", "NEW"),
                    dedupe_key=self._key_text(key),
                    payload_hash=item.get("payload_hash", ""),
                )
            rows = list(by_key.values())
            self._write_inbox(company_id, rows)
            return {
                "company_id": company_id,
                "source": source,
                "created": created,
                "updated": updated,
                "duplicate_skipped": duplicate_skipped,
                "duplicate_same_payload": duplicate_same_payload,
                "duplicate_success": duplicate_success,
                "retryable_existing": retryable_existing,
                "update_review": update_review,
                "total": len(rows),
            }

    def validate_raw(
        self,
        company_id: str,
        raw_records: list[dict[str, Any]],
        deputed_company_id: str = "",
        deputed_company_name: str = "",
    ) -> dict[str, Any]:
        """Validate incoming API/upload rows before they are persisted."""
        normalized = [
            self.normalize(company_id, raw, index, deputed_company_id, deputed_company_name)
            for index, raw in enumerate(raw_records)
        ]
        duplicate_map = duplicate_errors(normalized)
        results = []
        for row in normalized:
            errors = validate_record_all(row)
            if row["row_number"] in duplicate_map:
                errors.append({"cell": f"row {row['row_number']}", "message": duplicate_map[row["row_number"]]})
            if errors:
                results.append({"row": row["row_number"], "employee_name": row.get("name", ""), "valid": False, "errors": errors, "message": validation_summary(errors)})
        public_records = [
            {key: value for key, value in row.items() if key != "source_payload"}
            for row in normalized
        ]
        return {
            "total": len(normalized),
            "valid": len(normalized) - len(results),
            "invalid": len(results),
            "rows": results,
            "normalized_records": public_records,
        }

    def validate_mandatory_raw(
        self,
        company_id: str,
        raw_records: list[dict[str, Any]],
        deputed_company_id: str = "",
        deputed_company_name: str = "",
    ) -> dict[str, Any]:
        """Validate only the six PF creation fields before an Excel upload.

        Optional enrichment columns are accepted and preserved, but they must not
        block the mandatory-field gate used by the upload screen.
        """
        mandatory = {"uan", "aadhaar", "name", "dob", "doj", "basic_wage"}
        normalized = [
            self.normalize(company_id, raw, index, deputed_company_id, deputed_company_name)
            for index, raw in enumerate(raw_records)
        ]
        duplicate_map = duplicate_errors(normalized)
        results = []
        for row in normalized:
            errors = [item for item in validate_record_all(row) if item.get("column") in mandatory]
            if row["row_number"] in duplicate_map:
                errors.append({"cell": f"row {row['row_number']}", "message": duplicate_map[row["row_number"]]})
            if errors:
                results.append({"row": row["row_number"], "employee_name": row.get("name", ""), "valid": False, "errors": errors, "message": validation_summary(errors)})
        public_records = [
            {key: value for key, value in row.items() if key != "source_payload"}
            for row in normalized
        ]
        return {
            "total": len(normalized),
            "valid": len(normalized) - len(results),
            "invalid": len(results),
            "rows": results,
            "normalized_records": public_records,
            "scope": "mandatory_pf_fields",
        }

    def list_records(self, company_id: str, status: str = "", deputed_company_id: str = "") -> list[dict[str, Any]]:
        rows = self._read_inbox(company_id)
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if deputed_company_id:
            rows = [row for row in rows if value(row.get("deputed_company_id")) == value(deputed_company_id)]
        return [self._public_record(row) for row in rows]

    def records(self, company_id: str, record_ids: list[str] | None = None, deputed_company_id: str = "") -> list[dict[str, Any]]:
        rows = self._read_inbox(company_id)
        if record_ids:
            selected = set(record_ids)
            rows = [row for row in rows if row.get("record_id") in selected]
        if deputed_company_id:
            rows = [row for row in rows if value(row.get("deputed_company_id")) == value(deputed_company_id)]
        return rows

    def validate(self, company_id: str, record_ids: list[str] | None = None, deputed_company_id: str = "") -> dict[str, Any]:
        rows = self._read_inbox(company_id)
        selected = [row for row in rows if not record_ids or row.get("record_id") in record_ids]
        if deputed_company_id:
            selected = [row for row in selected if value(row.get("deputed_company_id")) == value(deputed_company_id)]
        duplicate_map = duplicate_errors(selected)
        results = []
        valid_ids = []
        for row in selected:
            errors = validate_record_all(row)
            if row.get("status") == "Registered - Pending Approval":
                errors.append({"cell": f"row {row['row_number']}", "message": "This row already reached Registered - Pending Approval and is blocked from resubmission."})
            for field in self.config(company_id).get("required_fields", []):
                if not value(row.get(field)):
                    errors.append({"cell": f"{field} (row {row['row_number']})", "message": "Required for this company configuration."})
            if row["row_number"] in duplicate_map:
                errors.append({"cell": f"row {row['row_number']}", "message": duplicate_map[row["row_number"]]})
            if errors:
                results.append({"record_id": row["record_id"], "row": row["row_number"], "employee_name": row.get("name", ""), "valid": False, "errors": errors, "message": validation_summary(errors)})
            else:
                valid_ids.append(row["record_id"])
                results.append({"record_id": row["record_id"], "row": row["row_number"], "employee_name": row.get("name", ""), "valid": True, "errors": [], "message": "Ready for insertion."})
        return {"company_id": company_id, "total": len(selected), "valid": len(valid_ids), "invalid": len(selected) - len(valid_ids), "valid_record_ids": valid_ids, "rows": results}

    def update_status(self, company_id: str, record_id: str, status: str, *, run_id: str = "", message: str = "") -> bool:
        """Persist the last supervised outcome for an inbox row."""
        with self.lock:
            rows = self._read_inbox(company_id)
            changed = False
            for row in rows:
                if row.get("record_id") != record_id:
                    continue
                previous_status = row.get("status", "")
                row["status"] = status
                row["last_run_id"] = run_id
                row["last_message"] = message
                changed_at = timestamp()
                row["last_completed_at"] = changed_at
                if status == "PROCESSING":
                    row["attempt_count"] = int(row.get("attempt_count", 0) or 0) + 1
                    row["last_attempt_at"] = changed_at
                if status in SUCCESS_STATUSES:
                    row["last_success_at"] = changed_at
                if status in {"FAILED", "VALIDATION_FAILED"}:
                    row["last_failure_at"] = changed_at
                self._history_event(
                    row,
                    status,
                    event="RECORD_STATUS_CHANGED",
                    message=message,
                    run_id=run_id,
                )
                self._audit(
                    "RECORD_STATUS_CHANGED",
                    company_id,
                    deputed_company_id=row.get("deputed_company_id", ""),
                    record_id=record_id,
                    employee_name=row.get("name", ""),
                    previous_status=previous_status,
                    status=status,
                    run_id=run_id,
                    message=message,
                )
                changed = True
                break
            if changed:
                self._write_inbox(company_id, rows)
            return changed

    def retry_record(
        self,
        company_id: str,
        record_id: str,
        *,
        deputed_company_id: str = "",
        approve_update: bool = False,
    ) -> dict[str, Any]:
        """Queue one failed row for a controlled retry.

        Successful rows are immutable. A changed successful payload is kept as
        ``pending_update`` until an operator explicitly approves reprocessing.
        """
        with self.lock:
            rows = self._read_inbox(company_id)
            for row in rows:
                if row.get("record_id") != record_id:
                    continue
                if deputed_company_id and value(row.get("deputed_company_id")) != value(deputed_company_id):
                    raise ValueError("Record is outside the selected deputed company scope.")
                status = row.get("status", "")
                if status in SUCCESS_STATUSES and not row.get("pending_update"):
                    raise ValueError("A successfully processed record cannot be submitted again.")
                if status == "UPDATE_REVIEW":
                    if not approve_update:
                        raise ValueError("Changed data is awaiting explicit reprocess approval.")
                    pending = row.get("pending_update")
                    if not isinstance(pending, dict):
                        raise ValueError("The pending update payload is missing and cannot be reprocessed safely.")
                    preserved = {
                        key: item for key, item in row.items()
                        if key in {
                            "record_id",
                            "first_seen_at",
                            "last_seen_at",
                            "last_source",
                            "attempt_count",
                            "retry_count",
                            "duplicate_count",
                            "status_history",
                            "last_attempt_at",
                            "last_success_at",
                            "last_failure_at",
                            "last_completed_at",
                            "last_run_id",
                            "last_message",
                        }
                    }
                    updated = dict(pending)
                    updated.update(preserved)
                    updated.pop("pending_update", None)
                    updated.pop("pending_update_hash", None)
                    updated["status"] = "RETRY_QUEUED"
                    updated["retry_count"] = int(row.get("retry_count", 0) or 0) + 1
                    updated["retry_queued_at"] = timestamp()
                    self._history_event(
                        updated,
                        "RETRY_QUEUED",
                        event="UPDATE_REPROCESS_APPROVED",
                        message="Changed source payload approved for explicit reprocessing.",
                    )
                    rows[rows.index(row)] = updated
                    self._write_inbox(company_id, rows)
                    self._audit("UPDATE_REPROCESS_APPROVED", company_id, deputed_company_id=updated.get("deputed_company_id", ""), record_id=record_id)
                    return self._public_record(updated)
                if status not in RETRYABLE_STATUSES:
                    raise ValueError(f"Record status {status or 'UNKNOWN'} is not eligible for retry.")
                row["status"] = "RETRY_QUEUED"
                row["retry_count"] = int(row.get("retry_count", 0) or 0) + 1
                row["retry_queued_at"] = timestamp()
                row["last_message"] = "Explicit retry queued; validation will run before browser submission."
                self._history_event(
                    row,
                    "RETRY_QUEUED",
                    event="RETRY_REQUESTED",
                    message=row["last_message"],
                )
                self._write_inbox(company_id, rows)
                self._audit("RETRY_REQUESTED", company_id, deputed_company_id=row.get("deputed_company_id", ""), record_id=record_id)
                return self._public_record(row)
            raise ValueError("Record not found in the selected company scope.")


PLATFORM = CompanyPlatform()
