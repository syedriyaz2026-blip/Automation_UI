import pytest

from ESIC.backend.esi_api_contract import (
    EsiApiContractError,
    validate_companies,
    validate_deputed_companies,
    validate_onboarded_employees,
)


def test_scope_contracts_require_parent_relationships():
    validate_companies({"records": [{"group_id": "g1", "company_id": "c1", "company_name": "Company"}]}, group_id="g1")
    validate_deputed_companies({"records": [{"company_id": "c1", "deputed_company_id": "d1", "deputed_company_name": "Scope"}]}, company_id="c1")


def test_employee_scope_is_fail_closed():
    with pytest.raises(EsiApiContractError):
        validate_onboarded_employees({"records": [{"employee_code": "E1", "name": "Employee", "doj": "01-01-2026", "company_id": "wrong", "deputed_company_id": "d1"}]}, company_id="c1", deputed_company_id="d1")
