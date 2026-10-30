from pathlib import Path

from ESIC.backend.esi_portal_workflow import (
    ARCHIVE_STAGES,
    EsiPortalWorkflow,
    EsiPortalWorkflowError,
    EsiWorkflowResult,
)


def test_archive_stages_match_documented_esi_trace():
    assert ARCHIVE_STAGES == (
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
    )


def test_workflow_requires_reviewed_selectors(tmp_path: Path):
    workflow = EsiPortalWorkflow(object(), selectors={}, artifact_dir=tmp_path)
    try:
        workflow._selector("login_username")
    except EsiPortalWorkflowError as exc:
        assert "login_username" in str(exc)
    else:
        raise AssertionError("Missing ESI selector should fail closed")


def test_result_is_serializable():
    result = EsiWorkflowResult(record_id="esi-1", status="ESI_REGISTERED")
    payload = result.as_dict()
    assert payload["record_id"] == "esi-1"
    assert payload["status"] == "ESI_REGISTERED"
