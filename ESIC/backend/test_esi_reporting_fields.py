from ESIC.backend.reporting.esi_report import build_esi_report


def test_esi_report_preserves_registration_fields_and_masks_sensitive_values():
    report = build_esi_report(
        [{
            "record_id": "esi-1",
            "company": "Company",
            "deputed_company": "Scope",
            "aadhaar": "123456789012",
            "mobile": "9876543210",
            "bank_account": "1234567890123456",
            "nominee_name": "Nominee",
            "dispensary_name": "Dispensary",
            "job_location": "Delhi",
            "status": "ESI_NEW",
        }],
        [], company_id="1", deputed_company_id="2",
    )
    row = report["records"][0]
    assert row["Nominee Name"] == "Nominee"
    assert row["Dispensary"] == "Dispensary"
    assert row["Job Location"] == "Delhi"
    assert row["Aadhaar (masked)"].endswith("9012")
    assert row["Bank Account (masked)"].endswith("3456")
