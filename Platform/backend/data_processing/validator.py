from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RULES = json.loads((ROOT / "config" / "validation.json").read_text(encoding="utf-8"))


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def as_text(value: object) -> str:
    """Convert a source value without silently trimming or changing it."""
    return "" if value is None else str(value)


def has_outer_whitespace(value: str) -> bool:
    return bool(value) and value != value.strip()


def add_whitespace_error(errors: list[dict[str, str]], record: dict[str, str], key: str) -> None:
    value = as_text(record.get(key, ""))
    if has_outer_whitespace(value):
        errors.append({
            "column": key,
            "cell": f"{key} (Excel row {record.get('row_number', '?')})",
            "message": "Leading or trailing spaces are not allowed; correct the source value before submission.",
        })


def parse_date(value: str) -> date | None:
    for fmt in RULES["date_formats"]:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def verhoeff_valid(value: str) -> bool:
    """Validate the Aadhaar-style Verhoeff checksum; does not call UIDAI."""
    multiplication = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,6,7,8,0,4,3,2,1],[6,5,7,8,9,1,0,2,3,4],[7,6,5,9,0,2,1,3,4,8],[8,7,9,0,6,3,2,4,1,5],[9,8,0,6,5,4,3,1,7,2]]
    permutation = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
    inverse = [0,4,3,2,1,5,6,7,8,9]
    total = 0
    for index, char in enumerate(reversed(value)):
        digit = int(char)
        total = multiplication[total][permutation[index % 8][digit]]
    return total == 0


def validate_record_all(record: dict[str, str]) -> list[dict[str, str]]:
    """Return every issue, each with a source column and human-readable message."""
    errors: list[dict[str, str]] = []
    def add(column: str, message: str):
        errors.append({"column": column, "cell": f"{column} (Excel row {record.get('row_number', '?')})", "message": message})

    for key in RULES["required_columns"]:
        add_whitespace_error(errors, record, key)

    for key in RULES["required_values"]:
        if not as_text(record.get(key, "")).strip():
            add(key, "Required value is missing.")
    sl_no = as_text(record.get("sl_no", "")).strip()
    if sl_no:
        if not re.fullmatch(r"\d+", sl_no) or int(sl_no) <= 0:
            add("sl_no", "Serial number must be a positive whole number.")
    emp_code = as_text(record.get("emp_code", "")).strip()
    if emp_code and (len(emp_code) > RULES["max_emp_code_length"] or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", emp_code)):
        add("emp_code", "Employee code must use letters, numbers, dot, underscore, slash, or hyphen only.")
    name = as_text(record.get("name", "")).strip()
    if name and (len(name) > RULES["max_name_length"] or not re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", name) or re.search(r"\s{2,}", name)):
        add("name", "Name must contain letters, spaces, apostrophes, periods, or hyphens only.")
    uan_source = as_text(record.get("uan", "")).strip()
    uan = digits(uan_source)
    if uan_source and not re.fullmatch(r"\d{12}", uan_source):
        add("uan", "UAN must contain exactly 12 digits with no spaces or separators.")
    aadhaar_source = as_text(record.get("aadhaar", "")).strip()
    aadhaar = digits(aadhaar_source)
    if aadhaar_source and not re.fullmatch(r"\d{12}", aadhaar_source):
        add("aadhaar", "Aadhaar must contain exactly 12 digits.")
    elif aadhaar and (aadhaar[0] == "0" or aadhaar[0] == "1"):
        add("aadhaar", "Aadhaar cannot start with 0 or 1.")
    elif aadhaar and not verhoeff_valid(aadhaar):
        add("aadhaar", "Aadhaar checksum is invalid; verify the number before submission.")
    for key in ("dob", "doj"):
        if as_text(record.get(key, "")).strip() and not parse_date(as_text(record[key])):
            add(key, "Date must be a real date in DD/MM/YYYY, DD-MM-YYYY, or YYYY-MM-DD format.")
    dob = parse_date(as_text(record.get("dob", "")))
    doj = parse_date(as_text(record.get("doj", "")))
    if dob and dob > date.today():
        add("dob", "Date of birth cannot be in the future.")
    if doj and doj > date.today():
        add("doj", "Date of joining cannot be in the future.")
    if dob and doj and doj < dob:
        add("doj", "Date of joining cannot be earlier than date of birth.")
    wage_source = as_text(record.get("basic_wage", "")).strip()
    wage = wage_source.replace(",", "")
    if wage:
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", wage):
            add("basic_wage", "Wage must be numeric.")
        else:
            amount = float(wage)
            if amount <= 0 or amount > RULES["max_wage"]:
                add("basic_wage", f"Wage must be greater than 0 and no more than {RULES['max_wage']}.")
    mobile_source = as_text(record.get("mobile", "")).strip()
    mobile = digits(mobile_source)
    if mobile_source and not re.fullmatch(r"[6-9]\d{9}", mobile_source):
        add("mobile", "Mobile must contain exactly 10 digits, start with 6-9, and have no spaces or separators.")
    email = as_text(record.get("email", "")).strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        add("email", "Email format is invalid.")
    gender = as_text(record.get("gender", "")).strip().lower()
    if gender and gender not in RULES["allowed_gender"]:
        add("gender", "Gender must be Male, Female, or Transgender.")
    marital = as_text(record.get("marital_status", "")).strip().lower()
    if marital and marital not in RULES["allowed_marital_status"]:
        add("marital_status", "Marital status is not one of the supported values.")
    spouse = as_text(record.get("husband_wife_name", "")).strip()
    if spouse and len(spouse) > RULES["max_name_length"]:
        add("husband_wife_name", "Husband/Wife Name is too long.")
    if marital in {"married", "widow", "widower", "divorced"} and not spouse:
        add("husband_wife_name", "Husband/Wife Name is required for the selected marital status.")
    return errors


def validation_summary(errors: list[dict[str, str]]) -> str:
    return " | ".join(f"{item['cell']}: {item['message']}" for item in errors)
