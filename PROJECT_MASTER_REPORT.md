# Automation UI — Project Master Report

**Purpose:** Consolidated single source of truth for project requirements, architecture, implementation decisions, fixes, configuration, verification, and remaining work.

**Last updated:** 2026-11-04 (Asia/Calcutta)

> This file must not be deleted or replaced. Every future implementation, fix, configuration change, and requirement decision must be appended to the changelog and reflected in the current-status sections.

## 1. Project scope

Automation UI is a statutory-operations platform with a shared Platform shell and independent statutory modules. The current active development scope in this chat is **Platform + PF only**. ESI work is being handled in another chat and must not be modified here.

The Platform UI is a monitoring and control surface. Source fetching, validation, queueing, background synchronization, retry tracking, portal automation, evidence capture, and status persistence are backend/runtime responsibilities.

## 2. Required repository structure

```text
Automation_UI/
├── Platform/
│   ├── frontend/
│   └── backend/
├── PF/
│   ├── frontend/
│   └── backend/
├── ESIC/
│   ├── frontend/
│   └── backend/
├── LWF/
│   ├── frontend/
│   └── backend/
└── PT/
    ├── frontend/
    └── backend/
```

The supplied `emanageadminapi-netcore` folder is a reference-only project. It is ignored by Git and must not be copied into or committed with the application.

## 3. Platform responsibilities

- Local login, JWT authentication, and protected APIs.
- Master Admin, Super Admin, Admin, and User role model.
- Company and deputed-company scope selection.
- Module and permission checks.
- Shared frontend shell and navigation.
- Source-data APIs and normalized intake contracts.
- Scoped inbox and employee queue.
- Validation, duplicate matching, update review, retries, and status lifecycle.
- Background synchronization health.
- Audit logs, evidence, reports, and run history.
- Local filesystem/SQLite operational storage.

## 4. PF source and scope model

### Active SQL source

- Server: `10.0.2.20`
- Port: `1433`
- Database: `Emanage_statutory`
- Access: read-only SQL queries through Microsoft ODBC Driver 18.
- The `.19` server is not used by the application.

### Company source

```text
dbo.im_companydetails
  int_cid              -> company ID
  varchar_companyname  -> company name
  varchar_companycode  -> company code
  varchar_ShortName    -> short name
```

Only records with `varchar_internalclient = 'Internal'` are returned by the company selector.

### Deputed-company source

```text
dbo.im_employeetransitionvalue
  int_cid             -> parent company
  int_transvalueid    -> deputed-company ID
  varchar_transvalue  -> deputed-company name
  int_transitionid=11 -> deputed-company relationship

dbo.im_subclient
  int_scid            -> deputed-company ID
  int_hoclient        -> head-office/company ID
  varchar_status      -> active scope filter
```

The API returns an `ALL` scope with ID `0` when deputed-company records exist. If no deputed company exists, company-only scope is allowed.

### PF employee source

```text
dbo.im_employeedetails
  identity, employee code, name, father name, gender, DOB, DOJ,
  Aadhaar, mobile, email, company, deputed company, status, updated time

dbo.im_employeepfdetails
  UAN, PF number, PF applicability, PF scheme details

dbo.im_clientaddemployee
  fixed/basic wage fallback

HR salary/salary-card tables
  basic-wage fallback sources
```

## 5. PF data flow

```text
Authenticate user
  -> fetch companies
  -> select company
  -> fetch deputed companies
  -> select deputed company or company-only scope
  -> fetch all source PF records for exact scope
  -> merge employee/PF/wage data
  -> normalize dates and identifiers
  -> validate mandatory fields
  -> match source identity and duplicates
  -> store scoped inbox records
  -> display PF Employee Queue
  -> optionally start controlled EPFO run
  -> capture statuses, evidence, retries, and reports
```

### PF mandatory fields

- Name
- UAN
- Aadhaar
- DOB
- DOJ
- Basic Wage

Invalid records remain visible with errors and cannot enter a valid processing run.

### PF identity and duplicate matching

Matching priority:

1. `source_employee_id` / source employee ID
2. UAN
3. Employee code
4. Aadhaar
5. Stable record fingerprint

Identical successful records are skipped. Changed records are marked for review. Failed/retryable records can be retried without creating a second employee record.

## 6. PF source APIs

The main source endpoints are:

```text
GET  /api/v1/source/companies
GET  /api/v1/source/companies/{company_id}/deputed
GET  /api/v1/source/companies/{company_id}/complete-data
POST /api/v1/companies/{company_id}/sync-source
GET  /api/v1/source/status
```

`sync-source` fetches all PF source records for the selected scope, converts them to the PF intake contract, validates them, deduplicates them, stores them, and returns the complete fetch/validation/ingest result. It does not silently submit records to EPFO.

## 7. PF processing flow

Only valid, selected, and scope-correct records can start an EPFO run.

Playwright:

1. Opens the configured EPFO portal.
2. Loads credentials for the exact company/deputed-company scope.
3. Fills username and password.
4. Detects the CAPTCHA/security checkpoint.
5. Waits for authorized manual CAPTCHA completion.
6. Verifies the authenticated dashboard.
7. Processes employees sequentially.
8. Records employee-level success/failure/pending status.
9. Captures screenshots, logs, timestamps, and evidence.

CAPTCHA recognition, solving, and submission are not automated or bypassed.

## 8. Background operation

The background monitor:

- Discovers SQL companies and deputed-company scopes.
- Fetches all PF source records.
- Validates and ingests records into the scoped inbox.
- Preserves historical records.
- Detects duplicates and changed source records.
- Exposes per-scope synchronization health.
- Can hand eligible PF records to the automatic run coordinator when enabled.

The supervisor script checks API/UI health every 30 seconds and restarts the local application when unavailable:

```text
Platform/backend/run_uat_supervisor.ps1
```

For machine-start operation, this supervisor still needs to be registered with the approved Windows Task Scheduler or Windows service account.

## 9. Runtime configuration

Important private configuration locations:

```text
Platform/backend/config/source_data.env
Platform/backend/config/portal_management.sqlite3
Platform/backend/config/company_configs.json
Platform/backend/config/uat_pf.example.env
```

Credentials must remain private. `source_data.env`, databases, runtime state, credentials, and secrets are Git-ignored.

UAT preparation files:

```text
Platform/backend/uat_preflight.ps1
docs/PF_UAT_DEPLOYMENT.md
Platform/backend/config/uat_pf.example.env
```

## 10. Current verified status

Verified on 2026-11-04:

- SQL `.20` network connection: working.
- SQL login to `Emanage_statutory`: working.
- Company fetch: working; 439 companies returned during verification.
- Deputed-company fetch: working.
- PF employee fetch: working.
- PF normalized complete-data contract: working.
- PF validation and duplicate matching: working.
- Background monitor implementation: present.
- Supervisor implementation: present.
- Backend PF tests: `53 passed`.
- Frontend production build: passed.
- API health: `ok` when started.
- Frontend health: HTTP `200` when started.

## 11. Known limitations and UAT actions

The following are deployment/UAT actions, not hidden assumptions:

1. Register the supervisor with Windows Task Scheduler or a Windows service manager.
2. Configure UAT JWT/API secrets.
3. Configure company-specific EPFO credentials.
4. Verify salary/basic-wage completeness for each target company.
5. Run a small PF canary batch.
6. Confirm manual CAPTCHA handoff and dashboard verification.
7. Confirm evidence/report download paths.
8. Approve `PF_BACKGROUND_AUTO_PROCESS_ENABLED=true` only after controlled UAT review.

## 12. Historical decisions

- Excel intake was removed from the PF/ESI user flow; source/API intake is the intended path.
- PF display is not restricted to only 48 hours or seven days; historical source records can be displayed.
- EPFO processing and source fetching are separate actions.
- Company-only scope is valid when no deputed-company scope exists.
- The source database is read-only; the application does not write to Emanage source tables.
- Portal CAPTCHA/security controls remain human-in-the-loop.
- ESI changes are out of scope for this chat and must be maintained in the separate ESI chat.

## 13. Change log

### 2026-11-04

- Created this consolidated master report.
- Documented PF/Platform source schema and end-to-end flow.
- Documented SQL `.20` as the active source.
- Documented UAT preparation, background operation, supervisor, and known limitations.

### 2026-11-04 (runtime improvements)

- Added PF background auto-processing handoff for eligible records.
- Added PF background portal-processing status.
- Added UAT supervisor restart script.
- Added UAT environment template and preflight script.
- Added UAT deployment checklist.

### 2026-11-03

- Corrected stale background scopes after source-mode changes.
- Corrected PF `None.isoformat()` handling when no joining cutoff is applied.
- Restarted the application in SQL-backed mode.

### 2026-10-31 to 2026-11-03

- Added local PF sample-source fallback for SQL repair periods.
- Added local ESI sample-source fallback in prior work; ESI is maintained outside this chat.
- Verified SQL `.20` connectivity and database login.
- Verified full combined test result of `60 passed` during local/ESI validation phase.

### Earlier project work

- Established the five-module repository structure.
- Implemented shared authentication, RBAC, scope selection, intake, validation, inbox, evidence, reports, and monitoring foundations.
- Implemented PF source aggregation, field-level source tracking, mandatory-field validation, duplicate handling, retry/update review, and Playwright portal boundaries.
- Removed the temporary Master-Admin-only PF restriction and restored role-based access.
- Kept `emanageadminapi-netcore` as a reference-only ignored folder.

## 14. Future update rule

Before any future task, read this report first. After every completed change:

1. Update the relevant current-status section.
2. Add a dated changelog entry.
3. Record tests and verification results.
4. Record remaining limitations or required operator actions.
5. Preserve all historical entries; do not delete or rewrite the report history.
