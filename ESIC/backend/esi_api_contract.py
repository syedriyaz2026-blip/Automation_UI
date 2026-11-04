"""Strict UAT contract checks for the ESI scope and onboarding APIs.

This module validates API payload shape without guessing route names or database
columns. It is intentionally fail-closed until the UAT API contract is wired.
"""

from __future__ import annotations

from typing import Any


class EsiApiContractError(ValueError):
    """Raised when an API response cannot be safely used by ESI."""


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EsiApiContractError(f"{label} response must be a JSON object.")
    return value


def _records(payload: Any, label: str) -> list[dict[str, Any]]:
    obj = _require_object(payload, label)
    records = obj.get("records", obj.get("data"))
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise EsiApiContractError(f"{label} response must contain a records/data array of objects.")
    return records


def _required(record: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    missing = [field for field in fields if record.get(field) in (None, "")]
    if missing:
        raise EsiApiContractError(f"{label} record is missing: {', '.join(missing)}")


def validate_groups(payload: Any) -> list[dict[str, Any]]:
    rows = _records(payload, "Group")
    for row in rows:
        _required(row, ("group_id", "group_name"), "Group")
    return rows


def validate_companies(payload: Any, *, group_id: str | int | None = None) -> list[dict[str, Any]]:
    rows = _records(payload, "Company")
    for row in rows:
        _required(row, ("company_id", "company_name"), "Company")
        if group_id not in (None, "") and str(row.get("group_id", group_id)) != str(group_id):
            raise EsiApiContractError("Company response contains a record outside the selected group scope.")
    return rows


def validate_deputed_companies(payload: Any, *, company_id: str | int | None = None) -> list[dict[str, Any]]:
    rows = _records(payload, "Deputed company")
    for row in rows:
        _required(row, ("deputed_company_id", "deputed_company_name", "company_id"), "Deputed company")
        if company_id not in (None, "") and str(row["company_id"]) != str(company_id):
            raise EsiApiContractError("Deputed-company response contains a record outside the selected company scope.")
    return rows


def validate_onboarded_employees(payload: Any, *, company_id: str | int, deputed_company_id: str | int) -> list[dict[str, Any]]:
    rows = _records(payload, "Onboarded employee")
    required = ("employee_code", "name", "doj", "company_id", "deputed_company_id")
    for row in rows:
        _required(row, required, "Onboarded employee")
        if str(row["company_id"]) != str(company_id) or str(row["deputed_company_id"]) != str(deputed_company_id):
            raise EsiApiContractError("Employee response contains a record outside the selected company/deputed-company scope.")
    return rows


def validate_scope(*, group_id: str | int, company_id: str | int, deputed_company_id: str | int) -> dict[str, str]:
    values = {"group_id": str(group_id), "company_id": str(company_id), "deputed_company_id": str(deputed_company_id)}
    if any(not value.strip() for value in values.values()):
        raise EsiApiContractError("Group, company, and deputed-company IDs are required for an ESI scope.")
    return values
