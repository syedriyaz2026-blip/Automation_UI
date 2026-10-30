import pytest

from esi_store import EsiPlatform
from test_esi_data import valid_esi_row


def test_esi_store_isolates_deputed_scopes_and_skips_identical_duplicates(tmp_path):
    store = EsiPlatform(tmp_path)
    first = store.ingest("company-1", [valid_esi_row()], source="esi_api", deputed_company_id="deputed-1")
    duplicate = store.ingest("company-1", [valid_esi_row()], source="esi_api", deputed_company_id="deputed-1")
    other_scope = store.ingest("company-1", [valid_esi_row(employee_code="ESI-002")], source="esi_api", deputed_company_id="deputed-2")

    assert first["created"] == 1
    assert duplicate["created"] == 0
    assert duplicate["duplicate_same_payload"] == 1
    assert len(store.list_records("company-1", deputed_company_id="deputed-1")) == 1
    assert len(store.list_records("company-1", deputed_company_id="deputed-2")) == 1
    assert len(store.list_records("company-2", deputed_company_id="deputed-1")) == 0
    assert other_scope["created"] == 1


def test_successful_esi_record_requires_review_for_changed_payload_and_explicit_approval(tmp_path):
    store = EsiPlatform(tmp_path)
    result = store.ingest("company-1", [valid_esi_row()], source="esi_api", deputed_company_id="deputed-1")
    record_id = result["records"][0]["record_id"]

    assert store.update_status("company-1", record_id, "ESI_REGISTERED", deputed_company_id="deputed-1")
    repeated = store.ingest("company-1", [valid_esi_row()], source="esi_api", deputed_company_id="deputed-1")
    changed = store.ingest("company-1", [valid_esi_row(name="Asha R.")], source="esi_api", deputed_company_id="deputed-1")

    assert repeated["duplicate_success"] == 1
    assert changed["update_review"] == 1
    with pytest.raises(ValueError, match="approve_update=true"):
        store.retry_record("company-1", record_id, deputed_company_id="deputed-1")
    updated = store.retry_record("company-1", record_id, deputed_company_id="deputed-1", approve_update=True)

    assert updated["status"] == "ESI_RETRY_QUEUED"
    assert updated["name"] == "Asha R."
    assert updated["retry_count"] == 1
