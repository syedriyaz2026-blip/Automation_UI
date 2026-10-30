from __future__ import annotations

import re


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def duplicate_errors(records: list[dict[str, str]]) -> dict[str, str]:
    """Return row-number keyed duplicate errors without modifying the input."""
    seen_uan: dict[str, str] = {}
    seen_aadhaar: dict[str, str] = {}
    errors: dict[str, str] = {}
    for record in records:
        row = record["row_number"]
        uan = digits(record.get("uan", ""))
        aadhaar = digits(record.get("aadhaar", ""))
        if uan and uan in seen_uan:
            errors[row] = f"Duplicate UAN in workbook; first appears on row {seen_uan[uan]}."
        elif uan:
            seen_uan[uan] = row
        if aadhaar and aadhaar in seen_aadhaar:
            errors[row] = f"Duplicate Aadhaar in workbook; first appears on row {seen_aadhaar[aadhaar]}."
        elif aadhaar:
            seen_aadhaar[aadhaar] = row
    return errors
