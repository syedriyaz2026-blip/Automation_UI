from __future__ import annotations

import scope_intake_settings
from openapi_spec import build_openapi


def test_intake_settings_are_isolated_by_company_and_deputed_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(scope_intake_settings, "DB_PATH", tmp_path / "settings.sqlite3")

    default = scope_intake_settings.get("company-a", "deputed-a")
    assert default["api_enabled"] is True
    assert default["excel_enabled"] is True

    saved = scope_intake_settings.save(
        "company-a",
        "deputed-a",
        api_enabled=False,
        excel_enabled=True,
        updated_by="admin",
    )
    assert saved["api_enabled"] is False
    assert saved["excel_enabled"] is True
    assert saved["updated_by"] == "admin"

    untouched_deputed_scope = scope_intake_settings.get("company-a", "deputed-b")
    untouched_company_scope = scope_intake_settings.get("company-b", "deputed-a")
    assert untouched_deputed_scope["api_enabled"] is True
    assert untouched_deputed_scope["excel_enabled"] is True
    assert untouched_company_scope["api_enabled"] is True
    assert untouched_company_scope["excel_enabled"] is True

    assert scope_intake_settings.list_for_companies({"company-a"}) == [saved]
    assert scope_intake_settings.list_for_companies({"company-b"}) == []


def test_openapi_documents_independent_intake_controls():
    document = build_openapi()
    settings_path = document["paths"]["/api/v1/companies/{company_id}/intake-settings"]
    assert settings_path["get"]["parameters"][-1]["$ref"].endswith("deputed_company_id")
    assert settings_path["post"]["requestBody"]["required"] is True
    assert "/api/v1/admin/intake-settings" in document["paths"]
    schema = document["components"]["schemas"]["IntakeSettings"]
    assert {"api_enabled", "excel_enabled"}.issubset(schema["properties"])
