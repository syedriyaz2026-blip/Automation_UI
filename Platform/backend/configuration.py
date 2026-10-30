"""Static configuration and selector-contract validation for local runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
CONFIG_ROOT = ROOT / "config"


def _read(name: str) -> dict:
    path = CONFIG_ROOT / name
    return json.loads(path.read_text(encoding="utf-8"))


def validate_configuration() -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    checked: list[str] = []
    try:
        selectors = _read("selectors.json")
        checked.append("selectors.json")
    except (OSError, json.JSONDecodeError) as exc:
        return {"valid": False, "errors": [f"selectors.json: {exc}"], "warnings": [], "checked": checked}

    if not selectors.get("schema_version"):
        errors.append("selectors.json: schema_version is required.")
    contracts = selectors.get("contracts", {})
    if not isinstance(contracts, dict) or not contracts:
        errors.append("selectors.json: at least one page contract is required.")
    else:
        for contract, keys in contracts.items():
            if not isinstance(keys, list) or not keys:
                errors.append(f"selectors.json: contract '{contract}' has no required selector keys.")
                continue
            for key in keys:
                if not isinstance(selectors.get(key), list) or not selectors.get(key):
                    errors.append(f"selectors.json: contract '{contract}' references missing selector '{key}'.")

    try:
        profile_registry = _read("selector_profiles.json")
        checked.append("selector_profiles.json")
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"selector_profiles.json: {exc}")
        profile_registry = {"profiles": {}}
    profiles = profile_registry.get("profiles", {})
    if not isinstance(profiles, dict) or not profiles:
        errors.append("selector_profiles.json: at least one selector profile is required.")

    try:
        companies = _read("company_configs.json").get("companies", [])
        checked.append("company_configs.json")
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"company_configs.json: {exc}")
        companies = []
    known_fields = {
        "sl_no", "emp_code", "company", "name", "doj", "dob", "aadhaar",
        "father_name", "marital_status", "husband_wife_name", "gender", "mobile",
        "email", "location", "basic_wage", "uan",
    }
    company_ids: set[str] = set()
    for company in companies:
        company_id = company.get("id", "<unknown>")
        if company_id in company_ids:
            errors.append(f"company_configs.json: duplicate company id '{company_id}'.")
        company_ids.add(company_id)
        if not str(company.get("name", "")).strip():
            errors.append(f"company '{company_id}': name is required.")
        if not str(company.get("deputed_company_name", "")).strip():
            errors.append(f"company '{company_id}': deputed_company_name is required.")
        profile = company.get("selector_profile", selectors.get("profile_name", ""))
        if profile not in profiles:
            errors.append(f"company '{company_id}': selector profile '{profile}' is not registered.")
        for field in ("portal_url", "register_url"):
            parsed = urlparse(str(company.get(field, "")))
            if parsed.scheme != "https" or not parsed.netloc:
                errors.append(f"company '{company_id}': {field} must be an HTTPS URL.")
            elif parsed.netloc != "unifiedportal-emp.epfindia.gov.in":
                errors.append(f"company '{company_id}': {field} must use the approved EPFO host.")
        selected = company.get("selected_fields", [])
        required = company.get("required_fields", [])
        for field in selected + required if isinstance(selected, list) and isinstance(required, list) else []:
            if field not in known_fields:
                errors.append(f"company '{company_id}': unknown field '{field}'.")
        if isinstance(selected, list) and isinstance(required, list):
            missing_selected = [field for field in required if field not in selected]
            if missing_selected:
                errors.append(f"company '{company_id}': required fields not selected: {', '.join(missing_selected)}.")
        else:
            errors.append(f"company '{company_id}': selected_fields and required_fields must be arrays.")
        if not company.get("required_fields"):
            warnings.append(f"company '{company_id}': required_fields is empty.")

    try:
        settings = _read("settings.json")
        checked.append("settings.json")
        if not str(settings.get("portal_url", "")).startswith("https://unifiedportal-emp.epfindia.gov.in/"):
            errors.append("settings.json: portal_url is not the approved EPFO HTTPS endpoint.")
        if int(settings.get("max_batch_records", 0)) <= 0:
            errors.append("settings.json: max_batch_records must be greater than zero.")
        if settings.get("storage_backend") != "filesystem":
            errors.append("settings.json: storage_backend must be 'filesystem' for the current local deployment.")
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"settings.json: {exc}")

    try:
        security = _read("security.json")
        checked.append("security.json")
        if security.get("algorithm") != "HS256":
            errors.append("security.json: algorithm must be HS256 for the local JWT implementation.")
        if int(security.get("access_token_ttl_seconds", 0)) < 60:
            errors.append("security.json: access_token_ttl_seconds must be at least 60 seconds.")
        if security.get("cookie_name") != "EPFO_JWT":
            errors.append("security.json: cookie_name must be EPFO_JWT.")
        if not os.getenv("EPFO_JWT_SECRET", ""):
            warnings.append("EPFO_JWT_SECRET is not set; the service will use a process-only secret and invalidate tokens on restart.")
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"security.json: {exc}")

    return {"valid": not errors, "errors": errors, "warnings": warnings, "checked": checked}
