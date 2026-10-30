from data_processing.validator import validate_record_all


VALID = {
    "row_number": "2",
    "sl_no": "1",
    "emp_code": "API001",
    "company": "Alp Consulting Ltd",
    "name": "Selva Manoj",
    "doj": "05/10/2026",
    "dob": "19/09/1997",
    "aadhaar": "762330210052",
    "father_name": "Aleem",
    "marital_status": "Un-Married",
    "husband_wife_name": "N/A",
    "gender": "Female",
    "mobile": "9742099082",
    "location": "Bengaluru",
    "basic_wage": "18600",
    "uan": "100000000001",
}


def messages(record):
    return " | ".join(item["message"] for item in validate_record_all(record))


def test_validation_rejects_outer_spaces_and_identifier_separators():
    record = dict(VALID, name=" Selva Manoj ", uan="1000 00000001", mobile="97420-99082")
    message = messages(record)
    assert "Leading or trailing spaces" in message
    assert "UAN must contain exactly 12 digits" in message
    assert "Mobile must contain exactly 10 digits" in message


def test_validation_rejects_future_joining_date_and_repeated_name_spaces():
    record = dict(VALID, name="Selva  Manoj", doj="31/12/2099")
    message = messages(record)
    assert "Name must contain" in message
    assert "Date of joining cannot be in the future" in message


def test_allowed_gender_and_marital_values_are_case_insensitive():
    record = dict(VALID, gender="female", marital_status="unmarried")
    assert validate_record_all(record) == []
