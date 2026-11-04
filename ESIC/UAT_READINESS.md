# ESIC UAT readiness

The ESI module now has a strict API contract gate in `backend/esi_api_contract.py`.
It validates the hierarchy:

```text
Group → Company → Deputed Company → Onboarded Employee
```

Before UAT deployment, provide the real UAT values for the placeholders in
`backend/config/uat.env.example`. Do not commit credentials.

Required UAT checks:

- Confirm the API base URL and four route templates.
- Confirm every response uses `records` or `data` arrays.
- Confirm IDs are returned as `group_id`, `company_id`, and `deputed_company_id`.
- Confirm employee records include `employee_code`, `name`, `doj`, and both scope IDs.
- Confirm the API account is read-only for the initial fetch phase.
- Confirm SQL Server `10.0.2.20` and the actual database/login through the UAT deployment secret store.
- Run the contract tests before enabling background execution.

The module remains fail-closed: missing routes, missing IDs, or cross-scope
records are rejected instead of being queued for ESI processing.
