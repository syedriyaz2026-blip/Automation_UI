from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

import api_service
import platform_backend


VALID = {
    "sl_no": "1",
    "emp_code": "API001",
    "company": "Alp Consulting Ltd",
    "name": "Selva",
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


def test_api_normalizes_aliases_and_accepts_valid_record():
    records, failures = api_service.validate_payload({"employees": [{"Employee Name": "Selva", **VALID}]})
    assert records[0]["name"] == "Selva"
    assert records[0]["row_number"] == "1"
    assert failures == []


def test_api_blocks_invalid_rows_before_browser_submission():
    bad = dict(VALID, name="123", aadhaar="123", uan="99", mobile="1", basic_wage="0")
    _, failures = api_service.validate_payload({"employees": [bad]})
    assert len(failures) == 1
    assert failures[0]["status"] == "Validation Failed"
    assert "Input Data Validation" == failures[0]["error_type"]
    assert "Aadhaar" in failures[0]["message"]


def test_datacore_registration_uses_portal_aadhaar_verification_at_every_queue_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_backend, "INBOX_ROOT", tmp_path / "inbox")
    monkeypatch.setattr(platform_backend, "AUDIT_LOG_PATH", tmp_path / "audit.jsonl")
    platform = platform_backend.CompanyPlatform()
    employee = {
        "UAN No": "100000000001",
        "Aadhar Number": "234567890123",
        "Name": "Test Employee",
        "DOB": "19/09/1997",
        "DOJ": "26/10/2026",
        "Basic Wage": "15000",
        "Company": "Datacore Technologies Pvt Ltd.",
    }

    assert platform.validate_raw("alp_datacore", [employee], "alp_datacore")["valid"] == 1
    assert platform.validate_mandatory_raw("alp_datacore", [employee], "alp_datacore")["valid"] == 1
    platform.ingest("alp_datacore", [employee], source="test", deputed_company_id="alp_datacore")
    assert platform.validate("alp_datacore", deputed_company_id="alp_datacore")["valid"] == 1
    assert platform.validate_raw("alp_consulting", [employee], "alp_consulting")["invalid"] == 1


def test_registration_json_upload_uses_selected_local_company_scope():
    employee = {"UAN No": "100000000001", "Aadhar Number": "234567890123", "Name": "Test Employee", "DOB": "19/09/1997", "DOJ": "26/10/2026", "Basic Wage": "15000", "Company": "Datacore Technologies Pvt Ltd."}
    document = json.dumps({"company_id": "alp_datacore", "employees": [employee]}).encode()
    boundary = "registration-test-boundary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"company_id\"\r\n\r\nalp_datacore\r\n".encode()
        + f"--{boundary}\r\nContent-Disposition: form-data; name=\"deputed_company_id\"\r\n\r\nalp_datacore\r\n".encode()
        + f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"member_registration.json\"\r\nContent-Type: application/json\r\n\r\n".encode()
        + document + f"\r\n--{boundary}--\r\n".encode()
    )
    handler = object.__new__(api_service.Handler)
    handler.path = "/api/v1/companies/alp_datacore/validate-upload"
    handler.headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))}
    handler.rfile = BytesIO(body)

    parsed = handler._multipart_run_body()

    assert parsed["records"] == [employee]
    assert parsed["company_id"] == "alp_datacore"
    assert parsed["deputed_company_id"] == "alp_datacore"
    handler.path = "/api/v1/companies/alp_consulting/validate-upload"
    handler.rfile = BytesIO(body)
    with pytest.raises(ValueError, match="matching the JSON file"):
        handler._multipart_run_body()


def test_api_detects_duplicate_uan_and_aadhaar():
    second = dict(VALID, sl_no="2", emp_code="API002", name="Manoj")
    _, failures = api_service.validate_payload({"employees": [VALID, second]})
    assert len(failures) == 1
    assert "Duplicate" in failures[0]["message"]


def test_api_rejects_unknown_run_fields():
    with pytest.raises(ValueError, match="Unsupported employee fields"):
        api_service.validate_payload({"employees": [dict(VALID, unexpected=True)]})


def test_api_enforces_configured_batch_limit(monkeypatch):
    monkeypatch.setattr(api_service, "configured_max_batch_records", lambda: 1)
    try:
        api_service.validate_payload({"employees": [VALID, dict(VALID, sl_no="2")]})
    except ValueError as exc:
        assert "configured maximum" in str(exc)
    else:
        raise AssertionError("Batch limit should reject oversized requests")


def test_run_store_does_not_persist_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(api_service, "RUN_ROOT", tmp_path)
    store = api_service.RunStore()
    run = store.create([api_service.normalize_record(VALID, 0)], {"mode": "MOCK", "username": "secret-user", "password": "secret-password"})
    saved = json.loads((tmp_path / run["run_id"] / "run.json").read_text(encoding="utf-8"))
    assert "password" not in json.dumps(saved)
    assert "username" not in json.dumps(saved)
    assert saved["stop_before_approval"] is True


def test_signin_only_run_authenticates_without_submitting_records(tmp_path, monkeypatch):
    monkeypatch.setattr(api_service, "RUN_ROOT", tmp_path)
    store = api_service.RunStore()
    monkeypatch.setattr(api_service, "STORE", store)
    run = store.create([], {"mode": "MOCK", "auth_only": True, "headless": True, "demo_delay_ms": 0, "typing_delay_ms": 0})

    api_service.run_worker(run["run_id"], "", "")

    finished = store.get(run["run_id"])
    assert finished["status"] == "COMPLETED"
    assert finished["auth_state"] == "REGISTRATION_READY"
    assert finished["total"] == finished["completed"] == 0
    assert finished["results"] == []
    assert not any(event["event"] == "ROW_STARTED" for event in finished["events"])


def test_datacore_record_reaches_pending_approval_in_full_mock_run(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_backend, "INBOX_ROOT", tmp_path / "inbox")
    monkeypatch.setattr(platform_backend, "AUDIT_LOG_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(api_service, "RUN_ROOT", tmp_path / "runs")
    platform = platform_backend.CompanyPlatform()
    store = api_service.RunStore()
    monkeypatch.setattr(api_service, "PLATFORM", platform)
    monkeypatch.setattr(api_service, "STORE", store)
    monkeypatch.setattr(api_service, "resolve_credentials", lambda *_args, **_kwargs: ("TEST_EMPLOYER", "TEST_PASSWORD"))
    monkeypatch.setattr(api_service.EpfoBot, "capture_evidence", lambda self, *_args: "")
    employee = {"UAN No": "100000000001", "Aadhar Number": "234567890123", "Name": "Test Employee", "DOB": "19/09/1997", "DOJ": "26/10/2026", "Basic Wage": "15000", "Company": "Datacore Technologies Pvt Ltd."}
    assert platform.validate_raw("alp_datacore", [employee], "alp_datacore")["valid"] == 1
    platform.ingest("alp_datacore", [employee], source="test", deputed_company_id="alp_datacore")
    record = platform.records("alp_datacore", deputed_company_id="alp_datacore")[0]
    assert platform.validate("alp_datacore", deputed_company_id="alp_datacore")["valid"] == 1
    run = store.create([record], {"mode": "MOCK", "company_id": "alp_datacore", "deputed_company_id": "alp_datacore", "headless": True, "demo_delay_ms": 0, "typing_delay_ms": 0})

    api_service.run_worker(run["run_id"], "", "")

    assert store.get(run["run_id"])["status"] == "COMPLETED"
    assert store.get(run["run_id"])["results"][0]["status"] == "Registered - Pending Approval"
    assert platform.records("alp_datacore", deputed_company_id="alp_datacore")[0]["status"] == "Registered - Pending Approval"


def test_run_store_persists_audit_and_error_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(api_service, "RUN_ROOT", tmp_path)
    store = api_service.RunStore()
    run = store.create([api_service.normalize_record(VALID, 0)], {"mode": "MOCK"})
    store.persist_error_log(run["run_id"], [{
        "row": "1",
        "employee_name": "Selva",
        "status": "Validation Failed",
        "error_type": "Input Data Validation",
        "workflow_step": "Pre-submission validation",
        "message": "Aadhaar is invalid.",
    }])
    store.event(run["run_id"], "ROW_FAILED", "HANDOVER_REQUIRED", message="Manual review required.")
    run_dir = tmp_path / run["run_id"]
    assert (run_dir / "audit.jsonl").exists()
    assert (run_dir / "error_log.jsonl").exists()
    errors = api_service.load_error_log(run["run_id"], run)
    assert errors["count"] == 1
    assert errors["errors"][0]["category"] == "VALIDATION_ERROR"


def test_local_host_guard():
    assert "127.0.0.1" in {"127.0.0.1", "localhost", "::1"}
    assert "0.0.0.0" not in {"127.0.0.1", "localhost", "::1"}


def test_configuration_contracts_are_valid():
    report = api_service.validate_configuration()
    assert report["valid"] is True
    assert report["errors"] == []


def test_company_inbox_persists_completion_and_blocks_resubmission(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_backend, "INBOX_ROOT", tmp_path)
    monkeypatch.setattr(platform_backend, "AUDIT_LOG_PATH", tmp_path / "audit.jsonl")
    platform = platform_backend.CompanyPlatform()
    platform.ingest("alp_consulting", [VALID])
    record = platform.records("alp_consulting")[0]
    assert platform.update_status("alp_consulting", record["record_id"], "Registered - Pending Approval", run_id="RUN-1", message="Completed")
    result = platform.validate("alp_consulting", [record["record_id"]])
    assert result["valid"] == 0
    assert "blocked from resubmission" in result["rows"][0]["message"]


def test_ingest_is_idempotent_and_failed_rows_are_explicitly_retryable(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_backend, "INBOX_ROOT", tmp_path)
    monkeypatch.setattr(platform_backend, "AUDIT_LOG_PATH", tmp_path / "audit.jsonl")
    platform = platform_backend.CompanyPlatform()

    first = platform.ingest("37", [VALID], source="api", deputed_company_id="3361")
    record = platform.records("37", deputed_company_id="3361")[0]
    repeated = platform.ingest("37", [VALID], source="api", deputed_company_id="3361")

    assert first["created"] == 1
    assert repeated["created"] == 0
    assert repeated["duplicate_skipped"] == 1
    assert repeated["duplicate_same_payload"] == 1
    assert platform.records("37", deputed_company_id="3361")[0]["record_id"] == record["record_id"]

    assert platform.update_status("37", record["record_id"], "FAILED", run_id="RUN-FAIL", message="Portal rejected the row.")
    failed_repeat = platform.ingest("37", [VALID], source="scheduled_api", deputed_company_id="3361")
    assert failed_repeat["retryable_existing"] == 1
    assert platform.records("37", deputed_company_id="3361")[0]["status"] == "FAILED"

    retried = platform.retry_record("37", record["record_id"], deputed_company_id="3361")
    assert retried["status"] == "RETRY_QUEUED"
    assert retried["retry_count"] == 1
    assert len(retried["status_history"]) >= 4

    platform.update_status("37", record["record_id"], "Registered - Pending Approval", run_id="RUN-OK", message="Accepted.")
    after_success = platform.ingest("37", [VALID], source="api", deputed_company_id="3361")
    assert after_success["duplicate_success"] == 1
    assert platform.records("37", deputed_company_id="3361")[0]["status"] == "Registered - Pending Approval"


def test_changed_success_payload_enters_update_review_until_approved(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_backend, "INBOX_ROOT", tmp_path)
    monkeypatch.setattr(platform_backend, "AUDIT_LOG_PATH", tmp_path / "audit.jsonl")
    platform = platform_backend.CompanyPlatform()
    platform.ingest("37", [VALID], source="api", deputed_company_id="3361")
    record = platform.records("37", deputed_company_id="3361")[0]
    platform.update_status("37", record["record_id"], "Registered - Pending Approval", run_id="RUN-OK", message="Accepted.")

    changed = dict(VALID, name="Updated Selva", basic_wage="19000")
    result = platform.ingest("37", [changed], source="api", deputed_company_id="3361")
    current = platform.records("37", deputed_company_id="3361")[0]

    assert result["update_review"] == 1
    assert current["status"] == "UPDATE_REVIEW"
    assert current["name"] == "Selva"
    assert current["pending_update"]["name"] == "Updated Selva"
    assert current["pending_update"]["basic_wage"] == "19000"
    with pytest.raises(ValueError, match="explicit reprocess approval"):
        platform.retry_record("37", record["record_id"], deputed_company_id="3361")

    approved = platform.retry_record("37", record["record_id"], deputed_company_id="3361", approve_update=True)
    assert approved["status"] == "RETRY_QUEUED"
    assert approved["name"] == "Updated Selva"
    assert approved["basic_wage"] == "19000"
    assert "pending_update" not in approved


def test_restart_marks_reserved_rows_for_recovery_without_overwriting_success(tmp_path, monkeypatch):
    inbox_root = tmp_path / "inbox"
    run_root = tmp_path / "runs"
    monkeypatch.setattr(platform_backend, "INBOX_ROOT", inbox_root)
    monkeypatch.setattr(platform_backend, "AUDIT_LOG_PATH", inbox_root / "audit.jsonl")
    monkeypatch.setattr(api_service, "RUN_ROOT", run_root)
    platform = platform_backend.CompanyPlatform()
    platform.ingest("37", [VALID], source="api", deputed_company_id="3361")
    record = platform.records("37", deputed_company_id="3361")[0]
    store = api_service.RunStore()
    run = store.create([record], {"mode": "REAL", "company_id": "37", "deputed_company_id": "3361"})
    store.event(run["run_id"], "ROW_STARTED", "PROCESSING", message="Browser submission started.")

    recovered_store = api_service.RunStore()
    assert recovered_store.snapshot(run["run_id"])["status"] == "RECOVERY_REQUIRED"
    assert platform.records("37", deputed_company_id="3361")[0]["status"] == "RECOVERY_REQUIRED"

    platform.update_status("37", record["record_id"], "Registered - Pending Approval", run_id="RUN-OK", message="Accepted before restart review.")
    successful_run = store.create([record], {"mode": "REAL", "company_id": "37", "deputed_company_id": "3361"})
    store.event(successful_run["run_id"], "ROW_STARTED", "PROCESSING", message="This row was already completed in the inbox.")
    api_service.RunStore()
    assert platform.records("37", deputed_company_id="3361")[0]["status"] == "Registered - Pending Approval"
