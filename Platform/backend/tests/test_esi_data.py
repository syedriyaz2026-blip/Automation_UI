from esi_data import ESI_REQUIRED_FIELDS, validate_esi_records


def valid_esi_row(**overrides):
    row = {
        "employee_code": "ESI-001",
        "name": "Asha Rao",
        "dob": "1996-05-31",
        "gender": "Female",
        "doj": "2026-10-28",
        "aadhaar": "643425495185",
        "mobile": "9398242890",
        "father_name": "Rao",
        "marital_status": "Unmarried",
        "present_address": "1 Main Road",
        "present_state": "Karnataka",
        "present_district": "Bengaluru Urban",
        "present_pincode": "560001",
        "permanent_address": "1 Main Road",
        "permanent_state": "Karnataka",
        "permanent_district": "Bengaluru Urban",
        "permanent_pincode": "560001",
        "nominee_name": "Rao",
        "nominee_relation": "Father",
        "nominee_address": "1 Main Road",
        "nominee_state": "Karnataka",
        "nominee_district": "Bengaluru Urban",
        "nominee_pincode": "560001",
        "dispensary_state": "Karnataka",
        "dispensary_district": "Bengaluru Urban",
        "dispensary_name": "ESI Bengaluru",
        "family_dispensary_state": "Karnataka",
        "family_dispensary_district": "Bengaluru Urban",
        "family_dispensary_name": "ESI Bengaluru",
        "bank_account": "123456789012",
        "bank_ifsc": "SBIN0001234",
        "bank_account_type": "Savings",
        "consent": "Yes",
    }
    row.update(overrides)
    return row


def test_esi_contract_requires_the_registration_fields_and_normalizes_dates():
    result = validate_esi_records([valid_esi_row()])

    assert result["invalid"] == 0
    assert result["valid"] == 1
    assert result["required_fields"] == ESI_REQUIRED_FIELDS
    assert result["valid_rows"][0]["dob"] == "31-05-1996"
    assert result["valid_rows"][0]["doj"] == "28-10-2026"


def test_esi_validation_reports_missing_fields_digits_and_duplicates():
    first = valid_esi_row()
    second = valid_esi_row(employee_code="ESI-002", aadhaar="1234", mobile="123")
    second["present_address"] = ""

    result = validate_esi_records([first, second])

    assert result["valid"] == 1
    assert result["invalid"] == 1
    messages = " ".join(item["message"] for item in result["rows"][0]["errors"])
    assert "Present address is required" in messages
    assert "Aadhaar must contain exactly 12 digits" in messages
    assert "Mobile must contain 10 digits" in messages


def test_esi_validation_detects_duplicate_identity_in_the_same_payload():
    result = validate_esi_records([valid_esi_row(), valid_esi_row(employee_code="ESI-002")])

    assert result["invalid"] == 1
    assert any("Duplicate aadhaar number" in item["message"] for item in result["rows"][0]["errors"])
