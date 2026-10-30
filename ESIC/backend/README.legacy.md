# ESIC backend boundary

The shared backend keeps ESI isolated from PF while reusing the common
security, persistence, authorization, and evidence primitives. The ESI
boundary is composed of:

- `backend/esi_data.py` for the ESIC field contract, required fields,
  conversions, and validation.
- `backend/esi_store.py` for scoped queue records, duplicate/update review,
  retries, status history, and audit events.
- `backend/esi_source.py` for the dedicated read-only EManage ESI employee
  source. It requires `EMANAGE_ESI_EMPLOYEE_ROUTE`, never falls back to the PF
  complete-data aggregator, and applies the latest 48-hour joining window.
- `backend/credential_vault.py` for encrypted, company/deputed-company and
  client/state/district-scoped ESI account mappings.
- `backend/esi_automation.py` for the explicit hand-off contract and evidence
  scope. The PF worker is never invoked for ESI records.

The routes are exposed by `backend/api_service.py`: contract/template,
validate-upload/upload, JSON ingest, source synchronization, scoped inbox and
audit APIs, automation status, reports, and administrator-only ESI account
metadata/configuration APIs. ESI records retain their employee, address,
nominee, dispensary, bank, and ESIC fields in the queue; sensitive values are
masked in reports.

The supplied `ESIC 1.zip` contains reports/assets and payload contracts but no
Java source or compiled portal worker. Consequently the integration is ready
for the existing worker to consume, but the portal execution step remains
explicitly pending until that worker is supplied. CAPTCHA remains a human
security checkpoint.
