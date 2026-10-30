from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from esi_source import EsiSourceDataService
from reporting.esi_report import build_esi_report


class FakeEsiApi:
    configured = True

    def __init__(self):
        self.calls = []

    def client_id(self, company_id):
        return 700 + int(company_id)

    def employee_esi_details(self, company_id, deputed_company_id, **kwargs):
        self.calls.append({"company_id": company_id, "deputed_company_id": deputed_company_id, **kwargs})
        now = datetime.now().replace(microsecond=0)
        return [
            {
                "EmployeeCode": "ESI-RECENT",
                "Name": "Recent Employee",
                "DOJ": (now - timedelta(hours=2)).isoformat(),
                "present_state": "Karnataka",
                "present_district": "Bengaluru Urban",
            },
            {
                "EmployeeCode": "ESI-OLD",
                "Name": "Historical Employee",
                "DOJ": (now - timedelta(hours=72)).isoformat(),
            },
        ]


def test_esi_source_uses_its_own_route_and_recent_joining_window(monkeypatch):
    monkeypatch.setenv("EMANAGE_ESI_EMPLOYEE_ROUTE", "/esi/employees")
    monkeypatch.setenv("ESI_RECENT_JOINING_ONLY", "true")
    monkeypatch.setenv("ESI_JOINING_WINDOW_HOURS", "48")
    api = FakeEsiApi()
    service = EsiSourceDataService(api)

    result = service.fetch(37, 3361, company_name="Alp Consulting Ltd", deputed_company_name="Alp Internal")

    assert [row["EmployeeCode"] for row in result["records"]] == ["ESI-RECENT"]
    assert result["client_id"] == "737"
    assert result["excluded_outside_window"] == 1
    assert result["route"] == "/esi/employees"
    assert api.calls[0]["client_id"] == 737
    assert result["records"][0]["company"] == "Alp Consulting Ltd"
    assert result["records"][0]["deputed_company"] == "Alp Internal"


def test_esi_source_honors_explicit_client_mapping(monkeypatch):
    monkeypatch.setenv("EMANAGE_ESI_EMPLOYEE_ROUTE", "/esi/employees")
    api = FakeEsiApi()
    EsiSourceDataService(api).fetch(37, 3361, client_id="901")
    assert api.calls[0]["client_id"] == 901


def test_esi_source_rejects_invalid_client_mapping(monkeypatch):
    monkeypatch.setenv("EMANAGE_ESI_EMPLOYEE_ROUTE", "/esi/employees")
    with pytest.raises(Exception, match="positive integer"):
        EsiSourceDataService(FakeEsiApi()).fetch(37, 3361, client_id="not-a-number")


def test_esi_source_requires_an_explicit_esi_endpoint(monkeypatch):
    monkeypatch.delenv("EMANAGE_ESI_EMPLOYEE_ROUTE", raising=False)
    with pytest.raises(Exception, match="EMANAGE_ESI_EMPLOYEE_ROUTE"):
        EsiSourceDataService(FakeEsiApi()).fetch(37, 3361)


def test_esi_report_is_scope_specific_and_does_not_expose_raw_payload():
    report = build_esi_report(
        [{
            "record_id": "esi-1",
            "company": "Alp Consulting Ltd",
            "deputed_company": "Alp Internal",
            "source": "esi_api",
            "employee_code": "ESI-001",
            "name": "Asha Rao",
            "aadhaar": "643425495185",
            "mobile": "9398242890",
            "status": "ESI_NEW",
            "source_payload": {"aadhaar": "643425495185"},
            "status_history": [{"timestamp": "2026-10-29T10:00:00+00:00"}],
        }],
        [{"timestamp": "2026-10-29T10:00:00+00:00", "event": "ESI_RECORD_ACCEPTED"}],
        company_id="37",
        deputed_company_id="3361",
    )

    serialized = json.dumps(report)
    assert "source_payload" not in serialized
    assert report["summary"]["company_id"] == "37"
    assert report["records"][0]["Aadhaar (masked)"].endswith("5185")
    assert report["records"][0]["Mobile"].endswith("2890")
