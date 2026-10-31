"""Continuous read-only source monitoring for PF and ESI.

This worker discovers Emanage scopes, fetches source records, validates and
stores them in the local monitoring inbox, and persists a per-scope health
snapshot. It never starts EPFO or ESIC portal automation.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BackgroundMonitor:
    def __init__(self, source_data, platform, esi_source, esi_store, root: Path):
        self.source_data = source_data
        self.platform = platform
        self.esi_source = esi_source
        self.esi_store = esi_store
        self.state_path = root / "background_sync_state.json"
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.state = self._load()
        self.interval_seconds = self._interval()
        self.thread = threading.Thread(target=self._loop, name="emanage-background-monitor", daemon=True)
        self.thread.start()

    @staticmethod
    def _enabled() -> bool:
        return os.getenv("EMANAGE_BACKGROUND_SYNC_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def _interval() -> int:
        try:
            return min(max(int(os.getenv("EMANAGE_BACKGROUND_SYNC_INTERVAL_SECONDS", "300")), 60), 86400)
        except ValueError:
            return 300

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {"service": {}}
        except (OSError, json.JSONDecodeError):
            return {"service": {}}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_path)

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "enabled": self._enabled(),
                "running": self.thread.is_alive(),
                "interval_seconds": self.interval_seconds,
                "portal_processing": False,
                "last_cycle": self.state.get("service", {}).get("last_cycle", ""),
                "last_error": self.state.get("service", {}).get("last_error", ""),
                "scopes": list(self.state.get("scopes", {}).values()),
            }

    def _set_scope(self, key: str, **values: Any) -> None:
        with self.lock:
            current = self.state.setdefault("scopes", {}).setdefault(key, {"scope": key})
            current.update(values)
            current["updated_at"] = _now()
            self._save()

    @staticmethod
    def _pf_raw(result: dict[str, Any], company: dict[str, Any], deputed: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for index, item in enumerate(result.get("records", []), start=1):
            raw = dict(item.get("fields") or {})
            raw.update(item.get("extra") or {})
            raw.update({
                "record_id": f"source-{item.get('employee_id') or item.get('employee_code') or index}",
                "source_employee_id": item.get("employee_id", ""),
                "source_row": index,
                "emp_code": item.get("employee_code", ""),
                "employee_code": item.get("employee_code", ""),
                "company": company.get("name", ""),
                "company_name": company.get("name", ""),
                "deputed_company_id": str(deputed.get("id", "")),
                "deputed_company": deputed.get("name", ""),
                "deputed_company_name": deputed.get("name", ""),
            })
            rows.append(raw)
        return rows

    def _sync_pf(self, company: dict[str, Any], deputed: dict[str, Any]) -> dict[str, Any]:
        company_id = int(company["id"])
        deputed_id = str(deputed.get("id", ""))
        result = self.source_data.complete(company_id, int(deputed_id) if deputed_id else None, limit=5000, recent_joining_only=False)
        raw = self._pf_raw(result, company, deputed)
        scan = self.platform.validate_raw(str(company_id), raw, deputed_company_id=deputed_id, deputed_company_name=deputed.get("name", ""))
        ingest = self.platform.ingest(str(company_id), raw, source="emanage_background", deputed_company_id=deputed_id, deputed_company_name=deputed.get("name", "")) if raw else {"created": 0, "updated": 0, "duplicate_skipped": 0}
        return {"module": "PF", "fetched": len(raw), "valid": scan.get("valid", 0), "invalid": scan.get("invalid", 0), "ingest": ingest, "status": "SUCCESS"}

    def _sync_esi(self, company: dict[str, Any], deputed: dict[str, Any]) -> dict[str, Any] | None:
        if not self.esi_source.configured:
            return None
        company_id = int(company["id"])
        deputed_id = int(deputed.get("id") or 0)
        result = self.esi_source.fetch(company_id, deputed_id, company_name=company.get("name", ""), deputed_company_name=deputed.get("name", ""), limit=5000)
        raw = result.get("records", [])
        scan = self.esi_store.validate_raw(str(company_id), raw, deputed_company_id=str(deputed_id), company_name=company.get("name", ""), deputed_company_name=deputed.get("name", ""))
        ingest = self.esi_store.ingest(str(company_id), raw, source="emanage_background_esi", deputed_company_id=str(deputed_id), company_name=company.get("name", ""), deputed_company_name=deputed.get("name", ""), allow_invalid=True) if raw else {"created": 0, "updated": 0}
        return {"module": "ESI", "fetched": len(raw), "valid": scan.get("valid", 0), "invalid": scan.get("invalid", 0), "ingest": ingest, "status": "SUCCESS"}

    def run_cycle(self) -> None:
        if not self._enabled():
            return
        cycle_started = _now()
        try:
            companies = self.source_data.companies().get("companies", [])
            for company in companies:
                deputed = self.source_data.deputed_companies(int(company["id"])).get("deputed_companies", [])
                scopes = deputed or [{"id": "", "name": company.get("name", "") }]
                for child in scopes:
                    key = f"{company['id']}:{child.get('id', '')}"
                    try:
                        pf = self._sync_pf(company, child)
                        esi = self._sync_esi(company, child)
                        self._set_scope(key, company_id=str(company["id"]), company_name=company.get("name", ""), deputed_company_id=str(child.get("id", "")), deputed_company_name=child.get("name", ""), last_success=cycle_started, last_error="", pf=pf, esi=esi)
                    except Exception as exc:
                        self._set_scope(key, company_id=str(company["id"]), company_name=company.get("name", ""), deputed_company_id=str(child.get("id", "")), deputed_company_name=child.get("name", ""), last_error=str(exc))
            with self.lock:
                self.state.setdefault("service", {}).update({"last_cycle": cycle_started, "last_error": ""})
                self._save()
        except Exception as exc:
            with self.lock:
                self.state.setdefault("service", {}).update({"last_cycle": cycle_started, "last_error": str(exc)})
                self._save()

    def _loop(self) -> None:
        # Run once shortly after startup, then continue at the configured interval.
        if self._enabled():
            self.run_cycle()
        while not self.stop_event.wait(self.interval_seconds):
            self.run_cycle()

