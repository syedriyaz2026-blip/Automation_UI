"""Dedicated ESI source adapter.

The PF source aggregator is intentionally not used here.  ESI employee data
has a different contract (address, nominee, dispensary, bank, consent and
ESIC account fields) and therefore needs its own source boundary.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any

from Platform.backend.source_data import EmanageApi, SourceDataError, _as_naive_datetime, _text


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class EsiSourceDataService:
    """Fetch only ESI-shaped employee rows for one selected scope."""

    def __init__(self, api: EmanageApi | None = None):
        self.api = api or EmanageApi()

    @property
    def configured(self) -> bool:
        return bool(self.api.configured and os.getenv("EMANAGE_ESI_EMPLOYEE_ROUTE", "").strip())

    def status(self) -> dict[str, Any]:
        return {
            "module": "ESI",
            "configured": self.configured,
            "api_configured": self.api.configured,
            "employee_route_configured": bool(os.getenv("EMANAGE_ESI_EMPLOYEE_ROUTE", "").strip()),
            "employee_route": os.getenv("EMANAGE_ESI_EMPLOYEE_ROUTE", "").strip(),
            "joining_window_hours": self._joining_window_hours(),
            "recent_joining_only": self._recent_only(),
            "read_only": True,
        }

    @staticmethod
    def _recent_only() -> bool:
        return os.getenv("ESI_RECENT_JOINING_ONLY", "true").strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def _joining_window_hours() -> int:
        try:
            requested = int(os.getenv("ESI_JOINING_WINDOW_HOURS", "48"))
        except ValueError:
            requested = 48
        return min(max(requested, 1), 48)

    @staticmethod
    def _date_value(row: dict[str, Any]) -> Any:
        for key in ("DOJ", "DateOfJoining", "JoiningDate", "doj", "date_of_joining"):
            if key in row and row[key] not in (None, ""):
                return row[key]
        return None

    def fetch(
        self,
        company_id: int,
        deputed_company_id: int,
        *,
        client_id: int | str | None = None,
        company_name: str = "",
        deputed_company_name: str = "",
        employee_code: str = "",
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not self.api.configured:
            raise SourceDataError("EMANAGE_API_BASE_URL is not configured.")
        route = os.getenv("EMANAGE_ESI_EMPLOYEE_ROUTE", "").strip()
        if not route:
            raise SourceDataError(
                "EMANAGE_ESI_EMPLOYEE_ROUTE is not configured; ESI employee data cannot be fetched safely."
            )

        if client_id not in (None, ""):
            try:
                resolved_client_id = int(str(client_id).strip())
            except (TypeError, ValueError) as exc:
                raise SourceDataError("ESI client_id must be a positive integer.") from exc
            if resolved_client_id <= 0:
                raise SourceDataError("ESI client_id must be a positive integer.")
        else:
            resolved_client_id = self.api.client_id(company_id) or company_id
        rows = self.api.employee_esi_details(
            company_id,
            deputed_company_id,
            client_id=resolved_client_id,
            employee_code=employee_code,
            limit=limit,
            offset=offset,
        )
        warnings: list[str] = []
        cutoff = datetime.now().replace(tzinfo=None) - timedelta(hours=self._joining_window_hours())
        selected: list[dict[str, Any]] = []
        excluded = 0
        for source_row in rows:
            row = dict(source_row)
            doj = _as_naive_datetime(self._date_value(row))
            if self._recent_only():
                if doj is None or doj < cutoff:
                    excluded += 1
                    continue
            row.setdefault("ClientId", resolved_client_id)
            if company_name:
                row.setdefault("company", company_name)
            if deputed_company_name:
                row.setdefault("deputed_company", deputed_company_name)
            row.setdefault("company_id", str(company_id))
            row.setdefault("deputed_company_id", str(deputed_company_id))
            selected.append(row)

        if excluded:
            warnings.append(f"{excluded} source row(s) were outside the latest {self._joining_window_hours()}-hour joining window and were not queued.")
        return {
            "records": selected,
            "count": len(selected),
            "source": "emanage_api.ESI",
            "route": route,
            "client_id": str(resolved_client_id),
            "queried_at": _now(),
            "warnings": warnings,
            "sources": {"employee_api": route, "contract": "ESIC empData.json"},
            "joining_window_hours": self._joining_window_hours() if self._recent_only() else None,
            "excluded_outside_window": excluded,
        }


ESI_SOURCE_DATA = EsiSourceDataService()

