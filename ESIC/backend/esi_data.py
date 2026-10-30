"""ESI employee-registration data contract and validation.

The supplied ESIC project contains the employee payload contract and execution
trace, but not its Java source.  This module keeps that contract explicit and
independent from the PF validator.  It normalizes source/API/Excel values into
the fields used by the ESI registration workflow and never attempts to solve a
CAPTCHA; a future browser worker must stop at that human security checkpoint.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Any


ESI_FIELDS = [
    "employee_code",
    "name",
    "dob",
    "gender",
    "doj",
    "aadhaar",
    "mobile",
    "father_name",
    "marital_status",
    "present_address",
    "present_state",
    "present_district",
    "present_pincode",
    "permanent_address",
    "permanent_state",
    "permanent_district",
    "permanent_pincode",
    "nominee_name",
    "nominee_relation",
    "nominee_address",
    "nominee_state",
    "nominee_district",
    "nominee_pincode",
    "dispensary_state",
    "dispensary_district",
    "dispensary_name",
    "family_dispensary_state",
    "family_dispensary_district",
    "family_dispensary_name",
    "bank_account",
    "bank_ifsc",
    "bank_account_type",
    "consent",
]

# These are portal-side actions/defaults rather than fields supplied by the
# archive's empData.json. They remain part of ESI_FIELDS for display and
# workflow mapping, but are not source-intake blockers.
ESI_PORTAL_ACTION_FIELDS = ["bank_account_type", "consent"]

ESI_REQUIRED_FIELDS = [
    "employee_code",
    "name",
    "dob",
    "gender",
    "doj",
    "aadhaar",
    "mobile",
    "father_name",
    "marital_status",
    "present_address",
    "present_state",
    "present_district",
    "present_pincode",
    "permanent_address",
    "permanent_state",
    "permanent_district",
    "permanent_pincode",
    "nominee_name",
    "nominee_relation",
    "nominee_address",
    "nominee_state",
    "nominee_district",
    "nominee_pincode",
    "dispensary_state",
    "dispensary_district",
    "dispensary_name",
    "family_dispensary_state",
    "family_dispensary_district",
    "family_dispensary_name",
    "bank_account",
    "bank_ifsc",
]

# These fields are present in the supplied ESIC employee contract and are
# preserved in the normalized queue even though they are not required to pass
# the registration-field gate.
ESI_OPTIONAL_FIELDS = [
    "client_id",
    "client_name",
    "job_location",
    "esic_sub_code",
    "remarks",
    "is_ip_registered",
    "alternate_mobile",
    "bank_name",
    "bank_branch",
    "pan",
    "employee_photo",
    "photo_name",
    "identity_document_name",
    "identity_document_path",
    "added_by_name",
    "added_by_email",
    "height",
    "width",
]

ESI_FIELDS = ESI_FIELDS + ESI_OPTIONAL_FIELDS

FIELD_LABELS = {
    "employee_code": "Employee code",
    "name": "Employee name",
    "dob": "Date of birth",
    "gender": "Gender",
    "doj": "Date of joining",
    "aadhaar": "Aadhaar number",
    "mobile": "Employee mobile",
    "father_name": "Father / husband name",
    "marital_status": "Marital status",
    "present_address": "Present address",
    "present_state": "Present state",
    "present_district": "Present district",
    "present_pincode": "Present pincode",
    "permanent_address": "Permanent address",
    "permanent_state": "Permanent state",
    "permanent_district": "Permanent district",
    "permanent_pincode": "Permanent pincode",
    "nominee_name": "Nominee name",
    "nominee_relation": "Nominee relationship",
    "nominee_address": "Nominee address",
    "nominee_state": "Nominee state",
    "nominee_district": "Nominee district",
    "nominee_pincode": "Nominee pincode",
    "dispensary_state": "Dispensary state",
    "dispensary_district": "Dispensary district",
    "dispensary_name": "Dispensary",
    "family_dispensary_state": "Family dispensary state",
    "family_dispensary_district": "Family dispensary district",
    "family_dispensary_name": "Family dispensary",
    "bank_account": "Bank account number",
    "bank_ifsc": "Bank IFSC",
    "bank_account_type": "Bank account type",
    "consent": "Registration consent",
    "client_id": "Client ID",
    "client_name": "Client name",
    "job_location": "Job location",
    "esic_sub_code": "ESIC sub-code",
    "remarks": "Remarks",
    "is_ip_registered": "Existing IP registration status",
    "alternate_mobile": "Alternate mobile",
    "bank_name": "Bank name",
    "bank_branch": "Bank branch",
    "pan": "PAN",
    "employee_photo": "Employee photo",
    "photo_name": "Employee photo name",
    "identity_document_name": "Identity document name",
    "identity_document_path": "Identity document path",
    "added_by_name": "Added by name",
    "added_by_email": "Added by email",
    "height": "Photo height",
    "width": "Photo width",
}


ESI_PORTAL_STAGES = [
    "Before Login",
    "After Login",
    "Duplicate Check",
    "Identity Details",
    "Address Details",
    "Nominee Details",
    "Bank Details",
    "Consent and Submission",
    "ESIC Number Retrieval",
    "Report and e-PehChan",
]


ALIASES: dict[str, tuple[str, ...]] = {
    "employee_code": ("employee_code", "EmployeeNo", "Emp_ID", "Emp Code", "emp_code", "Employee Code"),
    "name": ("name", "AssociateName", "Employee Name", "Emp Name", "Member Name"),
    "dob": ("dob", "DOB", "date_of_birth", "Date of Birth"),
    "gender": ("gender", "Gender"),
    "doj": ("doj", "DOJ", "Emp_DOJ", "date_of_joining", "Date of Joining"),
    "aadhaar": ("aadhaar", "AadhaarNo", "Aadhaar Number", "Aadhar Number", "aadhar"),
    "mobile": ("mobile", "Emp_Mob_Num", "Mobile", "Mobile Number", "AESICR_MobileNumber_of_Employee"),
    "father_name": ("father_name", "FatherName", "Father / Husband Name", "Father Name"),
    "marital_status": ("marital_status", "MaritalStatus", "Marital Status"),
    "present_address": ("present_address", "Presentaddress", "Present Address", "PresentAddress1"),
    "present_state": ("present_state", "Presentstate", "Present State"),
    "present_district": ("present_district", "Presentdistrict", "Present District"),
    "present_pincode": ("present_pincode", "Presentpincode", "Present Pincode"),
    "permanent_address": ("permanent_address", "Permanentaddress", "Permanent Address", "PermanentAddress1"),
    "permanent_state": ("permanent_state", "PermanentState", "Permanent State"),
    "permanent_district": ("permanent_district", "PermanentDistrict", "Permanent District"),
    "permanent_pincode": ("permanent_pincode", "PermanentPincode", "Permanent Pincode"),
    "nominee_name": ("nominee_name", "NominieeName", "Nominee Name"),
    "nominee_relation": ("nominee_relation", "NominieeRelationShipWithID", "Nominee Relation", "Relation"),
    "nominee_address": ("nominee_address", "NominieeAddress", "Nominee Address", "NominieeAddress1"),
    "nominee_state": ("nominee_state", "NominieeState", "Nominee State"),
    "nominee_district": ("nominee_district", "NominieeDistrict", "Nominee District"),
    "nominee_pincode": ("nominee_pincode", "NominieePincode", "Nominee Pincode"),
    "dispensary_state": ("dispensary_state", "AESUCR_Disp_State", "Dispensary State"),
    "dispensary_district": ("dispensary_district", "AESUCR_Disp_District", "Dispensary District"),
    "dispensary_name": ("dispensary_name", "AESUCR_DispName", "Dispensary Name"),
    "family_dispensary_state": ("family_dispensary_state", "AESICR_Family_Disp_State", "Family Dispensary State"),
    "family_dispensary_district": ("family_dispensary_district", "AESICR_Family_Disp_District", "Family Dispensary District"),
    "family_dispensary_name": ("family_dispensary_name", "AESICR_Family_DispName", "Family Dispensary Name"),
    "bank_account": ("bank_account", "AESICR_BankAccountNumber", "Bank Account", "BankAccNo", "Bank Account Number"),
    "bank_ifsc": ("bank_ifsc", "AESICR_IFSC_Code", "IFSC", "IFSC Code"),
    "bank_account_type": ("bank_account_type", "Bank Account Type", "Account Type"),
    "consent": ("consent", "Registration Consent", "Consent", "Terms Accepted"),
    "client_id": ("client_id", "ClientId", "Client ID"),
    "client_name": ("client_name", "ClientName", "Client Name"),
    "job_location": ("job_location", "JobLocation", "Job Location", "Location"),
    "esic_sub_code": ("esic_sub_code", "ESICSubCode", "ESIC Sub Code"),
    "remarks": ("remarks", "Remarks", "Remark"),
    "is_ip_registered": ("is_ip_registered", "IS_IPRegisterd", "IS_IPRegistered", "Existing IP Registered"),
    "alternate_mobile": ("alternate_mobile", "AESICR_Alternate_MobileNumber_of_Employee", "Alternate Mobile"),
    "bank_name": ("bank_name", "AESICR_BankName", "Bank Name"),
    "bank_branch": ("bank_branch", "AESICR_BankBranchName", "Bank Branch"),
    "pan": ("pan", "PanNo", "PAN", "PAN Number"),
    "employee_photo": ("employee_photo", "Employeephoto", "Employee Photo"),
    "photo_name": ("photo_name", "EmpPhotoname", "Photo Name"),
    "identity_document_name": ("identity_document_name", "IdentificationdocumentName", "Identity Document Name"),
    "identity_document_path": ("identity_document_path", "IdentificationdocumentPath", "Identity Document Path"),
    "added_by_name": ("added_by_name", "AddedByName", "Added By Name"),
    "added_by_email": ("added_by_email", "AddedByEmail", "Added By Email"),
    "height": ("height", "Height", "Photo Height"),
    "width": ("width", "Width", "Photo Width"),
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _lookup(source: dict[str, Any], names: tuple[str, ...]) -> Any:
    lowered = {str(key).strip().lower(): value for key, value in source.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return ""


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y")
    if isinstance(value, date):
        return value.strftime("%d-%m-%Y")
    raw = _text(value)
    if not raw:
        return ""
    for fmt in (
        "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%B-%Y",
        "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(raw[:19], fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    return raw


def _digits(value: Any) -> str:
    raw = _text(value)
    if re.fullmatch(r"\d+\.0+", raw):
        raw = raw.split(".", 1)[0]
    return re.sub(r"\D", "", raw)


def _bool_text(value: Any) -> str:
    raw = _text(value).lower()
    if raw in {"1", "true", "yes", "y", "accepted", "agree", "i agree"}:
        return "Yes"
    if raw in {"0", "false", "no", "n", "rejected"}:
        return "No"
    return _text(value)


def _address(source: dict[str, Any], field: str, fallback_names: tuple[str, ...]) -> str:
    direct = _lookup(source, ALIASES[field])
    if direct:
        return _text(direct)
    parts = [_lookup(source, (name,)) for name in fallback_names]
    return ", ".join(_text(part) for part in parts if _text(part))


def normalize_esi_record(
    raw: dict[str, Any],
    row_number: int,
    *,
    company_id: str = "",
    deputed_company_id: str = "",
    company_name: str = "",
    deputed_company_name: str = "",
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"Row {row_number} must be an object.")
    result: dict[str, Any] = {
        "row_number": str(raw.get("row_number") or raw.get("Row_Num1") or row_number),
        "company_id": str(company_id),
        "deputed_company_id": str(deputed_company_id),
        "company": _text(company_name) or _text(_lookup(raw, ("company", "ClientName", "Company"))),
        "deputed_company": _text(deputed_company_name) or _text(_lookup(raw, ("deputed_company", "Deputed Company"))),
    }
    for field in ESI_FIELDS:
        if field in {"present_address", "permanent_address", "nominee_address"}:
            fallback = {
                "present_address": ("PresentAddress1", "PresentAddress2", "PresentAddress3"),
                "permanent_address": ("PermanentAddress1", "PermanentAddress2", "PermanentAddress3"),
                "nominee_address": ("NominieeAddress1", "NominieeAddress2", "NominieeAddress3"),
            }[field]
            value = _address(raw, field, fallback)
        else:
            value = _lookup(raw, ALIASES[field])
        if field in {"dob", "doj"}:
            value = _date(value)
        elif field in {"aadhaar", "mobile", "alternate_mobile", "present_pincode", "permanent_pincode", "nominee_pincode", "bank_account"}:
            value = _digits(value)
        elif field in {"pan", "bank_ifsc"}:
            value = _text(value).upper()
        elif field == "consent":
            value = _bool_text(value)
        else:
            value = _text(value)
        result[field] = value
    result["source_payload"] = dict(raw)
    result["source_hash"] = hashlib.sha256(_stable_payload(result).encode("utf-8")).hexdigest()
    return result


def _stable_payload(record: dict[str, Any]) -> str:
    return "|".join(f"{field}={record.get(field, '')}" for field in ESI_FIELDS)


def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%d-%m-%Y")
        return True
    except (TypeError, ValueError):
        return False


def validate_esi_records(
    raw_records: list[dict[str, Any]],
    *,
    company_id: str = "",
    deputed_company_id: str = "",
    company_name: str = "",
    deputed_company_name: str = "",
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    seen: dict[str, dict[str, int]] = {
        "employee_code": {},
        "aadhaar": {},
        "mobile": {},
    }
    for index, raw in enumerate(raw_records or [], start=1):
        item = normalize_esi_record(
            raw,
            index,
            company_id=company_id,
            deputed_company_id=deputed_company_id,
            company_name=company_name,
            deputed_company_name=deputed_company_name,
        )
        normalized.append(item)
        errors: list[dict[str, str]] = []
        for field in ESI_REQUIRED_FIELDS:
            if not _text(item.get(field)):
                errors.append({"column": field, "cell": f"{FIELD_LABELS[field]} (row {index})", "message": f"{FIELD_LABELS[field]} is required."})
        for field in ("dob", "doj"):
            if item.get(field) and not _valid_date(item[field]):
                errors.append({"column": field, "cell": f"{FIELD_LABELS[field]} (row {index})", "message": "Use DD-MM-YYYY or a valid Excel date."})
        if item.get("aadhaar") and not re.fullmatch(r"\d{12}", item["aadhaar"]):
            errors.append({"column": "aadhaar", "cell": f"Aadhaar Number (row {index})", "message": "Aadhaar must contain exactly 12 digits."})
        if item.get("mobile") and not re.fullmatch(r"[6-9]\d{9}", item["mobile"]):
            errors.append({"column": "mobile", "cell": f"Mobile (row {index})", "message": "Mobile must contain 10 digits and start with 6, 7, 8, or 9."})
        if item.get("alternate_mobile") and not re.fullmatch(r"[6-9]\d{9}", item["alternate_mobile"]):
            errors.append({"column": "alternate_mobile", "cell": f"Alternate mobile (row {index})", "message": "Alternate mobile must contain 10 digits and start with 6, 7, 8, or 9."})
        for field in ("present_pincode", "permanent_pincode", "nominee_pincode"):
            if item.get(field) and not re.fullmatch(r"\d{6}", item[field]):
                errors.append({"column": field, "cell": f"{FIELD_LABELS[field]} (row {index})", "message": "Pincode must contain exactly 6 digits."})
        if item.get("bank_account") and not re.fullmatch(r"\d{6,30}", item["bank_account"]):
            errors.append({"column": "bank_account", "cell": f"Bank account (row {index})", "message": "Bank account must contain 6 to 30 digits."})
        if item.get("bank_ifsc") and not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", item["bank_ifsc"].upper()):
            errors.append({"column": "bank_ifsc", "cell": f"Bank IFSC (row {index})", "message": "IFSC must match the standard 11-character format."})
        if item.get("pan") and not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", item["pan"].upper()):
            errors.append({"column": "pan", "cell": f"PAN (row {index})", "message": "PAN must match the standard 10-character format."})
        for field in ("employee_code", "aadhaar", "mobile"):
            key = item.get(field)
            if key:
                duplicate_key = str(key).lower()
                previous_row = seen[field].get(duplicate_key)
                if previous_row is not None:
                    errors.append({"column": "duplicate", "cell": f"row {index}", "message": f"Duplicate {FIELD_LABELS[field].lower()} matches row {previous_row}."})
                else:
                    seen[field][duplicate_key] = index
        if errors:
            rows.append({"row": index, "employee_name": item.get("name", ""), "valid": False, "errors": errors, "message": "; ".join(error["message"] for error in errors)})
    public = [{key: value for key, value in item.items() if key != "source_payload"} for item in normalized]
    # Validation rows use the normalized list position for reporting, while
    # ``row_number`` preserves the source workbook's original Row_Num1.  Do
    # not compare those two coordinate systems: an archive workbook can start
    # at any source row and an invalid row must never leak into valid_rows.
    invalid_indexes = {int(row["row"]) for row in rows}
    valid_rows = [item for index, item in enumerate(public, start=1) if index not in invalid_indexes]
    return {
        "total": len(normalized),
        "valid": len(normalized) - len(rows),
        "invalid": len(rows),
        "rows": rows,
        "normalized_records": public,
        "required_fields": ESI_REQUIRED_FIELDS,
        "scope": "mandatory_esi_registration_fields",
        "all_rows_normalized": public,
        "valid_rows": valid_rows,
    }


def esi_contract() -> dict[str, Any]:
    return {
        "module": "ESI",
        "label": "Employee State Insurance",
        "portal_url": "https://portal.esic.gov.in/EmployerPortal/ESICInsurancePortal/Portal_Loginnew.aspx",
        "fields": [{"key": field, "label": FIELD_LABELS[field], "required": field in ESI_REQUIRED_FIELDS} for field in ESI_FIELDS],
        "required_fields": ESI_REQUIRED_FIELDS,
        "stages": ESI_PORTAL_STAGES,
        "captcha_policy": "Human confirmation is required at the portal CAPTCHA checkpoint.",
        "source_contract": "ESIC empData.json and execution log supplied in ESIC 1.zip",
    }

