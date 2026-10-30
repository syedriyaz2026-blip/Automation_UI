"""Company/deputed-company scoped ESI inbox.

ESI records live in their own files and lifecycle so a PF submission can never
be mixed with an ESI registration.  The store is deliberately filesystem
local, matching the current local deployment, and records every state change
with a UTC timestamp for audit and recovery.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ESIC.backend.esi_data import validate_esi_records


ROOT = Path(__file__).resolve().parent
INBOX_ROOT = ROOT / "api_inbox"
AUDIT_LOG_PATH = INBOX_ROOT / "esi_audit.jsonl"
SUCCESS_STATUSES = {"ESI_COMPLETED", "ESI_REGISTERED"}
RETRYABLE_STATUSES = {"ESI_FAILED", "ESI_VALIDATION_FAILED", "ESI_RECOVERY_REQUIRED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _safe_file(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", _text(value))
    return safe or "unknown"


def _fingerprint(record: dict[str, Any]) -> str:
    identity = "|".join(
        _text(record.get(key)).lower()
        for key in ("employee_code", "aadhaar", "mobile", "name", "dob", "doj")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class EsiPlatform:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or INBOX_ROOT)
        self.audit_path = self.root / "esi_audit.jsonl"
        self.lock = threading.RLock()

    def _path(self, company_id: str) -> Path:
        return self.root / f"esi_{_safe_file(company_id)}.json"

    def _read(self, company_id: str) -> list[dict[str, Any]]:
        path = self._path(company_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []

    def _write(self, company_id: str, rows: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(company_id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _audit(self, event: str, company_id: str, **details: Any) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        item = {"timestamp": _now(), "module": "ESI", "event": event, "company_id": str(company_id), **details}
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in row.items() if key != "source_payload"}

    def validate_raw(
        self,
        company_id: str,
        raw_records: list[dict[str, Any]],
        *,
        deputed_company_id: str = "",
        company_name: str = "",
        deputed_company_name: str = "",
    ) -> dict[str, Any]:
        return validate_esi_records(
            raw_records,
            company_id=str(company_id),
            deputed_company_id=str(deputed_company_id),
            company_name=company_name,
            deputed_company_name=deputed_company_name,
        )

    def list_records(self, company_id: str, *, deputed_company_id: str = "", status: str = "") -> list[dict[str, Any]]:
        rows = self._read(company_id)
        if deputed_company_id:
            rows = [row for row in rows if _text(row.get("deputed_company_id")) == _text(deputed_company_id)]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return [self._public(row) for row in rows]

    def audit_events(self, company_id: str, *, deputed_company_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        """Return newest ESI lifecycle events for the exact selected scope."""
        if not self.audit_path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _text(item.get("company_id")) != _text(company_id):
                continue
            if deputed_company_id and _text(item.get("deputed_company_id")) != _text(deputed_company_id):
                continue
            events.append(item)
            if len(events) >= max(1, min(int(limit or 200), 1000)):
                break
        return events

    def records(self, company_id: str, *, deputed_company_id: str = "", record_ids: list[str] | None = None) -> list[dict[str, Any]]:
        rows = self._read(company_id)
        if deputed_company_id:
            rows = [row for row in rows if _text(row.get("deputed_company_id")) == _text(deputed_company_id)]
        if record_ids:
            selected = set(record_ids)
            rows = [row for row in rows if row.get("record_id") in selected]
        return rows

    def ingest(
        self,
        company_id: str,
        raw_records: list[dict[str, Any]],
        *,
        source: str,
        deputed_company_id: str = "",
        company_name: str = "",
        deputed_company_name: str = "",
        allow_invalid: bool = False,
    ) -> dict[str, Any]:
        scan = self.validate_raw(
            company_id,
            raw_records,
            deputed_company_id=deputed_company_id,
            company_name=company_name,
            deputed_company_name=deputed_company_name,
        )
        invalid_rows = {item["row"] for item in scan["rows"]}
        if scan["invalid"] and not allow_invalid:
            raise ValueError("ESI payload failed validation; invalid rows were not stored.")
        accepted = [
            item for item in scan["normalized_records"]
            if int(item["row_number"]) not in invalid_rows or allow_invalid
        ]
        with self.lock:
            rows = self._read(company_id)
            by_identity = {
                self._identity_key(row): index
                for index, row in enumerate(rows)
                if _text(row.get("deputed_company_id")) == _text(deputed_company_id)
            }
            result = {
                "created": 0,
                "duplicate_skipped": 0,
                "duplicate_same_payload": 0,
                "duplicate_success": 0,
                "retryable_existing": 0,
                "update_review": 0,
                "validation_failed": scan["invalid"],
                "records": [],
                "validation": scan,
            }
            validation_by_row = {item["row"]: item for item in scan["rows"]}
            for item in accepted:
                key = self._identity_key(item)
                item_hash = _text(item.get("source_hash"))
                existing_index = by_identity.get(key)
                if existing_index is None:
                    invalid = validation_by_row.get(int(item["row_number"]))
                    record = self._new_record(
                        item,
                        source,
                        company_id,
                        deputed_company_id,
                        status="ESI_VALIDATION_FAILED" if invalid else "ESI_NEW",
                        validation_errors=invalid.get("errors", []) if invalid else [],
                    )
                    if key:
                        by_identity[key] = len(rows)
                    rows.append(record)
                    result["created"] += 1
                    result["records"].append(self._public(record))
                    self._audit("ESI_RECORD_ACCEPTED", company_id, deputed_company_id=deputed_company_id, record_id=record["record_id"], source=source)
                    continue
                existing = rows[existing_index]
                existing["duplicate_count"] = int(existing.get("duplicate_count", 0) or 0) + 1
                existing["last_seen_at"] = _now()
                existing["last_source"] = source
                if existing.get("source_hash") == item_hash:
                    result["duplicate_skipped"] += 1
                    result["duplicate_same_payload"] += 1
                    if existing.get("status") in SUCCESS_STATUSES:
                        result["duplicate_success"] += 1
                    elif existing.get("status") in RETRYABLE_STATUSES:
                        result["retryable_existing"] += 1
                    self._history(existing, "ESI_DUPLICATE_SKIPPED", "Repeated ESI data was not submitted twice.", source)
                elif existing.get("status") in SUCCESS_STATUSES:
                    existing["pending_update"] = self._public(item)
                    existing["pending_update_hash"] = item_hash
                    existing["status"] = "ESI_UPDATE_REVIEW"
                    result["update_review"] += 1
                    self._history(existing, "ESI_UPDATE_REVIEW", "Changed data is waiting for explicit reprocess approval.", source)
                else:
                    validation = validation_by_row.get(int(item["row_number"]))
                    item.update({
                        "record_id": existing.get("record_id") or self._record_id(item, company_id, deputed_company_id),
                        "first_seen_at": existing.get("first_seen_at", _now()),
                        "attempt_count": existing.get("attempt_count", 0),
                        "retry_count": existing.get("retry_count", 0),
                        "duplicate_count": existing.get("duplicate_count", 0),
                        "status_history": existing.get("status_history", []),
                        "status": "ESI_VALIDATION_FAILED" if validation else "ESI_NEW",
                        "validation_errors": validation.get("errors", []) if validation else [],
                    })
                    rows[existing_index] = item
                    self._history(item, "ESI_NEW", "Changed data replaced the previous retryable payload.", source)
                    result["created"] += 1
                    result["records"].append(self._public(item))
            self._write(company_id, rows)
            return result

    @staticmethod
    def _identity_key(row: dict[str, Any]) -> str:
        for field in ("employee_code", "aadhaar", "mobile"):
            value = _text(row.get(field))
            if value:
                return field + ":" + value.lower()
        # Invalid rows without any stable identity must not collapse into one
        # another.  The row number keeps repeated uploads of the same source
        # row idempotent while preserving separate correction rows.
        return "fingerprint:" + _fingerprint(row) + ":row:" + _text(row.get("row_number"))

    def _record_id(self, row: dict[str, Any], company_id: str, deputed_company_id: str) -> str:
        seed = f"ESI|{company_id}|{deputed_company_id}|{self._identity_key(row)}"
        return "esi-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:18]

    def _new_record(
        self,
        item: dict[str, Any],
        source: str,
        company_id: str,
        deputed_company_id: str,
        *,
        status: str = "ESI_NEW",
        validation_errors: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        timestamp = _now()
        record = dict(item)
        record.update({
            "record_id": self._record_id(item, company_id, deputed_company_id),
            "status": status,
            "source": source,
            "last_source": source,
            "first_seen_at": timestamp,
            "last_seen_at": timestamp,
            "attempt_count": 0,
            "retry_count": 0,
            "duplicate_count": 0,
            "status_history": [],
            "validation_errors": list(validation_errors or []),
        })
        self._history(record, status, "ESI record stored for correction before ESI registration." if validation_errors else "Validated ESI record queued for the ESI workflow.", source)
        return record

    @staticmethod
    def _history(row: dict[str, Any], status: str, message: str, source: str = "") -> None:
        row.setdefault("status_history", []).append({"timestamp": _now(), "status": status, "event": status, "message": message, "source": source})

    def validate(self, company_id: str, *, record_ids: list[str] | None = None, deputed_company_id: str = "") -> dict[str, Any]:
        selected = self.records(company_id, deputed_company_id=deputed_company_id, record_ids=record_ids)
        results = []
        valid_ids = []
        for row in selected:
            scan = validate_esi_records([row], company_id=company_id, deputed_company_id=deputed_company_id, company_name=row.get("company", ""), deputed_company_name=row.get("deputed_company", ""))
            errors = scan["rows"][0]["errors"] if scan["rows"] else []
            if row.get("status") in SUCCESS_STATUSES:
                errors.append({"column": "status", "cell": f"row {row.get('row_number')}", "message": "This ESI record has already completed and is blocked from resubmission."})
            if errors:
                results.append({"record_id": row["record_id"], "row": row.get("row_number"), "employee_name": row.get("name", ""), "valid": False, "errors": errors, "message": "; ".join(item["message"] for item in errors)})
            else:
                valid_ids.append(row["record_id"])
                results.append({"record_id": row["record_id"], "row": row.get("row_number"), "employee_name": row.get("name", ""), "valid": True, "errors": [], "message": "Ready for ESI registration."})
        return {"company_id": company_id, "deputed_company_id": deputed_company_id, "total": len(selected), "valid": len(valid_ids), "invalid": len(selected) - len(valid_ids), "valid_record_ids": valid_ids, "rows": results}

    def update_status(self, company_id: str, record_id: str, status: str, *, deputed_company_id: str = "", message: str = "") -> bool:
        with self.lock:
            rows = self._read(company_id)
            for row in rows:
                if row.get("record_id") != record_id or (_text(deputed_company_id) and _text(row.get("deputed_company_id")) != _text(deputed_company_id)):
                    continue
                row["status"] = status
                row["last_message"] = message
                row["last_completed_at"] = _now()
                if status == "ESI_PROCESSING":
                    row["attempt_count"] = int(row.get("attempt_count", 0) or 0) + 1
                    row["last_attempt_at"] = _now()
                if status in SUCCESS_STATUSES:
                    row["last_success_at"] = _now()
                if status in RETRYABLE_STATUSES:
                    row["last_failure_at"] = _now()
                self._history(row, status, message)
                self._write(company_id, rows)
                self._audit("ESI_RECORD_STATUS_CHANGED", company_id, deputed_company_id=deputed_company_id, record_id=record_id, status=status, message=message)
                return True
        return False

    def retry_record(self, company_id: str, record_id: str, *, deputed_company_id: str = "", approve_update: bool = False) -> dict[str, Any]:
        """Move one failed ESI row back to the explicit pending state.

        Changed data for an already successful registration is never silently
        replaced; the caller must approve the ``ESI_UPDATE_REVIEW`` payload.
        """
        with self.lock:
            rows = self._read(company_id)
            for row in rows:
                if row.get("record_id") != record_id or (_text(deputed_company_id) and _text(row.get("deputed_company_id")) != _text(deputed_company_id)):
                    continue
                status = _text(row.get("status"))
                if status == "ESI_UPDATE_REVIEW":
                    if not approve_update:
                        raise ValueError("Changed data requires explicit approve_update=true before reprocessing.")
                    pending = row.pop("pending_update", None)
                    row.pop("pending_update_hash", None)
                    if not pending:
                        raise ValueError("No pending update is available for this ESI record.")
                    preserved = {key: row.get(key) for key in ("record_id", "first_seen_at", "attempt_count", "retry_count", "duplicate_count", "status_history")}
                    row.clear()
                    row.update(pending)
                    row.update(preserved)
                    row["source"] = "approved_update"
                elif status not in RETRYABLE_STATUSES and status not in {"ESI_NEW", "ESI_VALIDATION_FAILED"}:
                    raise ValueError(f"ESI record in status {status or 'unknown'} cannot be retried.")
                row["status"] = "ESI_RETRY_QUEUED"
                row["retry_count"] = int(row.get("retry_count", 0) or 0) + 1
                row["last_retry_at"] = _now()
                row["last_message"] = "ESI record queued for a controlled retry."
                self._history(row, "ESI_RETRY_QUEUED", row["last_message"], "retry")
                self._write(company_id, rows)
                self._audit("ESI_RECORD_RETRY_QUEUED", company_id, deputed_company_id=deputed_company_id, record_id=record_id)
                return self._public(row)
        raise ValueError("Record not found for the selected Company and Deputed Company scope.")


ESI_STORE = EsiPlatform()

