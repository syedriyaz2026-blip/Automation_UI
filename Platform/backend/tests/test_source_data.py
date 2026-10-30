from __future__ import annotations

from source_data import CompleteEmployeeDataService, EmanageApi, ReadOnlyDatabase, _envelope_items


def test_source_api_envelope_is_unwrapped():
    payload = [{"Status": 1, "data": [{"EmployeeCode": "E1"}, {"EmployeeCode": "E2"}]}]
    assert [row["EmployeeCode"] for row in _envelope_items(payload)] == ["E1", "E2"]


def test_source_status_is_read_only_and_disabled_without_environment(monkeypatch):
    for key in ("PF_READONLY_DB_CONNECTION", "PF_SOURCE_DB_CONNECTION", "EMANAGE_API_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    status = CompleteEmployeeDataService(ReadOnlyDatabase(), EmanageApi()).status()
    assert status["read_only"] is True
    assert status["available_sources"] == []
