# ESI module

The ESI module is isolated from the existing PF inbox and worker. Every row is stored under the exact `(company_id, deputed_company_id)` scope in `api_inbox/esi_<company>.json`, with lifecycle events in `api_inbox/esi_audit.jsonl`.

## Intake lifecycle

1. Select the ESI module, Company, and Deputed Company.
2. Choose either Excel upload or API intake. The existing per-scope intake switches apply independently to ESI.
3. Normalize source aliases and dates to `DD-MM-YYYY`.
4. Validate every required ESI registration field, row format, identifiers, pincode, bank account, IFSC, and optional PAN/mobile formats.
5. Keep invalid source-sync rows visible as `ESI_VALIDATION_FAILED`; workbook/API intake rejects invalid payloads before storage.
6. Skip identical repeats, isolate different deputed-company scopes, and put changed data for a completed row into `ESI_UPDATE_REVIEW`.
7. Retry failed rows explicitly; changed completed rows require `approve_update=true`.
8. Expose rows through `/api/v1/esi/inbox` and lifecycle records through `/api/v1/esi/audit`.
9. Download the exact scope report through `/api/v1/esi/report?company=...&deputed_company_id=...&format=xlsx` or `format=json`.

## Source and account boundaries

ESI source synchronization uses `EMANAGE_ESI_EMPLOYEE_ROUTE` through
`backend/esi_source.py`. It never calls the PF complete-data aggregator. The
route accepts an optional `client_id`, `state`, and `district`; when the client
is omitted, the adapter resolves the company client ID from the read-only
source API. Source rows are limited to the rolling latest 48-hour joining
window by default (`ESI_JOINING_WINDOW_HOURS`, clamped to 48).

ESI credentials are mapped independently by client, state, and district from
the Configuration/Admin panel. The `/api/v1/admin/esi/accounts` endpoints
return metadata only. Passwords are encrypted in the local credential vault
and are never placed in the UI response, report, audit event, or source row.

The ESI queue has an expandable **View all ESI fields** detail for each row so
address, nominee, dispensary, bank, ESIC, and source contract fields remain
visible after conversion. Only rows that pass the module validator can be
uploaded or queued for automation; duplicate payloads are counted and skipped,
while changed completed records enter `ESI_UPDATE_REVIEW`.

## ESI contract

The normalized field contract comes from the supplied `empData.json`, monthly-report headers, and execution log in `ESIC 1.zip`. The API is documented at `/api/v1/esi/contract`; the generated workbook is available at `/api/v1/esi/template`.

The supplied archive did not contain the original Java/Selenium implementation—only its project structure and execution log—so the ESI intake layer deliberately returns `PENDING_AUTOMATION` after storage. It does not invoke the PF/EPFO worker or invent portal selectors. The portal URL and recorded stages are exposed in the contract, and the CAPTCHA checkpoint remains a human security step until a separately supplied, reviewable ESI worker is integrated.
