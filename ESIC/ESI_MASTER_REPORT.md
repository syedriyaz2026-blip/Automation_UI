# ESI Master Report

**Project path:** `D:\Automation_UI\ESIC`  
**Module:** ESIC / Employee State Insurance  
**Report role:** Single source of truth for requirements, decisions, implementation, fixes, configuration, verification, and remaining work.  
**Last updated:** 2026-11-04 (Asia/Calcutta)

## 1. Working rules

- This chat and project scope are limited to the dedicated ESIC module.
- This report must be read before new ESIC work begins.
- New work must be appended to the history and reflected in the current status.
- Historical entries must not be deleted or replaced.
- Credentials, passwords, tokens, and private connection strings must not be committed.
- The platform UI is for monitoring/showcase; the background machine owns unattended execution.

## 2. Project objective

Recreate the ESI business workflow represented by `ESIC 1.zip`, while providing a new company/deputed-company monitoring UI in the platform. The monitoring layer must show real background processing results; it must not require an operator to start or stop the machine process.

Target hierarchy:

```text
Group → Company → Deputed Company → Onboarded Employee → ESI Processing → Evidence/Reports
```

## 3. Source reference and limitations

Reference archive:

```text
C:\Users\RiyazBashaSyed\Downloads\ESIC 1.zip
```

The archive contains `empData.json`, `Credentials.json`, Maven/TestNG metadata, reports, screenshots, CAPTCHA assets, OpenCV/ML-related folders, logs, and generated artifacts. No recoverable `.java` or `.class` files were found in the archive. Therefore, exact original selectors, CAPTCHA behavior, dispensary portal mapping, and e-PehChan/email implementation cannot be claimed as verified.

CAPTCHA bypass or CAPTCHA-solving automation is not part of the implementation. A compliant human security checkpoint is supported.

## 4. Required ESI workflow

1. Select group/company/deputed-company scope.
2. Fetch newly onboarded employees from the approved API/database source.
3. Normalize the ESI employee contract.
4. Validate required fields and data formats.
5. Detect duplicates and changed successful records.
6. Store accepted records in the scoped ESI inbox.
7. Background machine processes eligible records continuously.
8. ESIC portal workflow: Before Login, After Login, Duplicate Check, Identity Details, Address Details, Nominee Details, Dispensary Details, Bank Details, Consent and Submission, ESIC Number Retrieval, Report and e-PehChan.
9. Capture status, audit events, evidence, portal reference, and reports.
10. Platform displays live results only; it does not own worker start/stop control.

## 5. Current implementation

### Backend

- `backend/esi_data.py`: ESI fields, aliases, normalization, validation, contract metadata, stage definitions.
- `backend/esi_source.py`: API/local source adapter, company/deputed-company scope filtering, client ID resolution, pagination, latest-joining-window filtering.
- `backend/esi_store.py`: company-scoped JSON inbox, duplicate/update/retry lifecycle, status history, audit events.
- `backend/esi_portal_workflow.py`: configurable Playwright workflow skeleton with explicit selectors, stage evidence, portal reference extraction, success-marker validation, and human CAPTCHA checkpoint.
- `backend/esi_automation.py`: worker/account readiness bridge; deliberately does not invoke the PF worker.
- `backend/reporting/esi_report.py`: masked JSON/Excel reports, summary, records, and audit events.
- `backend/esi_api_contract.py`: fail-closed validation for Group → Company → Deputed Company → Onboarded Employee API responses.

### Frontend

- `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`: ESI monitoring dashboard.
- Company and deputed-company scope controls.
- Queue, progress, process-stage, audit/evidence, and report views.
- API health and queue integration through `frontend/api.js`.
- Automatic refresh every 30 seconds.
- No manual start/stop control in the monitoring UI.
- Sample records are only an offline fallback when the API is unavailable.

### Configuration and documentation

- `backend/config/selector_profile.example.json`: example only; production selectors are not verified.
- `backend/config/uat.env.example`: UAT configuration template without secrets.
- `UAT_READINESS.md`: UAT prerequisites and contract checks.
- `CONTINUOUS_MONITORING.md`: unattended monitoring design.

## 6. API/data contract

Required source data:

| Domain | Required data |
|---|---|
| Group | `group_id`, `group_name`, active state |
| Company | `company_id`, `company_name`, `group_id`, active state |
| Deputed company | `deputed_company_id`, `deputed_company_name`, `company_id`, active state |
| Onboarded employee | employee identity, `doj`, `company_id`, `deputed_company_id` |
| ESI details | address, nominee, dispensary, bank, Aadhaar/mobile, document/photo references |
| Existing status | ESI status, ESIC number, portal reference, timestamps |
| Monitoring | stage, attempts, retries, message, evidence/report links |

The current ESIC source adapter calls external `EmanageApi` methods for client and employee data. The actual UAT route names and database schema remain to be verified.

## 7. Database/server decision

The requested SQL Server target is:

```text
10.0.2.20:1433
```

Network reachability and TCP port 1433 were confirmed. The attempted SQL login using `sa` was rejected with SQL error 18456. No database enumeration or schema read was completed. The existing `ESIC/backend/config/portal_management.sqlite3` is only for portal credentials/accounts and is not the employee/company source database.

**Decision:** use only `10.0.2.20` for the SQL source once a working authorized read-only login and exact database/schema are confirmed. Do not guess the database or table mapping.

## 8. UAT readiness

Current state: **Not deployment-ready for real UAT processing.**

Completed preparation:

- UAT environment template.
- Strict scope/response contract validation.
- Monitoring UI/API connection layer.
- Continuous refresh behavior.
- Fail-closed behavior for missing or cross-scope records.

Remaining UAT blockers:

- Working authorized SQL/API credentials.
- Exact database name and schema on `10.0.2.20`.
- Verified Group, Company, Deputed Company, and onboarding API routes.
- Real UAT base URL and secrets configured outside source control.
- Production selector profile and portal-worker deployment.
- Exact dispensary portal mapping.
- Verified e-PehChan/email process.
- End-to-end UAT run and test evidence.

## 9. Verification record

- JavaScript syntax checks passed for `frontend/app.js` and `frontend/api.js`.
- ESI contract/unit tests were added.
- The broader pytest invocation previously reached teardown but did not complete cleanly; this remains a verification issue.
- Live SQL authentication has not succeeded.
- Live ESIC portal execution has not been verified.

## 10. Decisions and constraints

- Work only within `D:\Automation_UI\ESIC` for ESIC implementation changes.
- Do not mix PF worker or PF selectors into ESI.
- Do not claim the attached Java process is reproduced exactly when its source is unavailable.
- Do not bypass or solve CAPTCHA.
- Do not start continuous execution until source/API/database fetch is verified.
- The platform is a monitoring/showcase surface; the background worker is the execution authority.

## 11. Change history

### 2026-11-04

- Created this master report as the ESIC single source of truth.
- Added UAT API contract validation and UAT environment template.
- Added continuous-monitoring documentation and removed manual start/stop behavior from the monitoring UI.
- Connected the UI to configurable ESI health, queue, audit, report, and monitor API paths.
- Confirmed SQL server network reachability for `10.0.2.20:1433`; SQL authentication with supplied `sa` credentials failed.

### 2026-11-03

- Added ESI monitoring dashboard structure for company/deputed-company scope.
- Added queue, process-stage timeline, audit/evidence, and reports views.

### Earlier work

- Established ESI data contract, source adapter, scoped store, portal workflow skeleton, reporting, validation tests, and explicit human CAPTCHA checkpoint.
- Compared the current module against the attached archive and recorded missing source/selector limitations.

## 12. Next approved work sequence

1. Verify authorized read-only access to `10.0.2.20`.
2. Enumerate databases and identify the correct statutory/onboarding source.
3. Map Group → Company → Deputed Company relationships.
4. Verify onboarding employee API/database fields and pagination.
5. Implement the real fetch adapter and contract tests.
6. Connect the monitoring UI to real API responses.
7. Add continuous worker integration and recovery behavior.
8. Complete UAT validation before enabling live execution.

