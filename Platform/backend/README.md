# EPFO Member Registration Automation

The application architecture and corrected operating flow are documented in ARCHITECTURE.md. The user-provided reference video and extracted frames are isolated under reference/video/ and are not loaded by the runtime.

This tool automates the EPFO employer-portal **registration only** workflow. The primary integration is a local API consumed by the React portal: employee records arrive through API or Excel intake, are normalized and validated before submission, and are processed sequentially. The older Excel/Tkinter launcher remains available as a compatibility path while existing workbook users migrate.

## New local desktop-style UI

The repository is split into two clear application areas: `frontend/` contains the React/Vite control surface, while `backend/` contains the Python API, Playwright worker, configuration, validation, reporting, tests, and local runtime stores.

~~~powershell
cd backend
python backend/api_service.py
cd ..\frontend
npm install
npm run dev
~~~

Open the portal at `http://127.0.0.1:5201`. The desktop UI calls the local API and includes company selection, API inbox review, validation, supervised run monitoring, manual CAPTCHA preview, error handover, and evidence links. Portal selectors and browser workflow code remain in Python/configuration files.

For one-command local operation, use start_local.ps1 and stop_local.ps1. The launcher stops only this project’s API/Vite workers, leaves unrelated project ports untouched, and verifies readiness before reporting the URLs.

To verify the local operator UI after a frontend change, start the local services and use the browser manually. The active browser automation is Playwright-based and is kept under `backend/automation/`.

## API-first service

Start the combined backend and frontend:

```powershell
cd backend
python backend/api_service.py
```

Open the React frontend at `http://127.0.0.1:5201`; the backend API listens at `http://127.0.0.1:8792` and exposes these endpoints:

- `POST /api/v1/auth/login` — validates local application credentials and returns an HS256 JWT plus an HttpOnly cookie.
- `GET /api/v1/auth/me` and `POST /api/v1/auth/logout` — inspect or clear the current JWT session.
- `POST /api/v1/runs` — accepts `{ "mode": "MOCK|REAL", "employees": [...] }` and starts one sequential run.
- `GET /api/v1/runs/{run_id}` — returns authentication state, row progress, results, errors, and event history.
- `POST /api/v1/runs/{run_id}/stop` — stops at the next safe row boundary.
- `POST /api/v1/runs/{run_id}/continue` — requests an immediate manual-authentication state check.
- `GET /api/v1/schema` — returns the accepted employee fields.
- `GET /api/v1/config/validate` — validates selector contracts, company profiles, and approved portal configuration before a run.

Errors use the stable shape `{ "error": { "code": "VALIDATION_ERROR", "message": "...", "details": [] } }` so clients can display a useful message without parsing free-form text. Run requests reject unknown fields and are limited by `config/settings.json:max_batch_records` before browser startup.

The portal requires a local application login. Set `EPFO_UI_USER`, `EPFO_UI_PASSWORD`, and `EPFO_JWT_SECRET` before a real local deployment; the seeded local accounts are for first-run development only and can be managed by Super Admin. After login, select a company/deputed-company scope, choose API or Excel intake, review normalized records, validate them, and use **Start insertion** only after every selected row is valid. Company mappings are maintained in `config/company_configs.json`, so a source API may provide extra fields without changing the EPFO insertion workflow.

Local deployment uses the local API plus SQLite for portal-management users, scoped intake switches, and encrypted credential records. Inbox data is stored under `api_inbox/`, run state under `api_runs/`, and screenshot/evidence blobs under `blob_storage/`. No Azure Blob, S3, or remote write service is contacted. The source company/employee database remains read-only.

Each company/deputed-company scope has an independent PF portal profile in `config/company_configs.json`, including its portal URLs and credential environment-variable names. Credentials are never placed in that JSON. For local testing they can be entered in the Configuration panel and are encrypted with Windows DPAPI inside the local `portal_management.sqlite3` database, or supplied through the configured environment variables (for example `EPFO_ALP_CONSULTING_USER` and `EPFO_ALP_CONSULTING_PASSWORD`). A run selects credentials only by its exact company/deputed-company scope; credentials are excluded from persisted run state and evidence.

Additional platform endpoints:

- `GET /api/v1/companies` and `GET /api/v1/companies/{id}/schema` — company dropdown and field mapping.
- `GET /api/v1/source/status` — reports which read-only source adapters are configured.
- `GET /api/v1/source/companies` — returns the Emanage internal-company scope (`varchar_internalclient = 'Internal'`) with the source `company_id` and company code.
- `GET /api/v1/source/companies/{company_id}/deputed` — returns deputed companies using Emanage's `int_cid -> int_scid` relationship from `im_employeetransitionvalue` and `im_subclient`. The response includes the parent company ID, deputed company ID, and an `ALL` option with ID `0` when available.
- `GET /api/v1/source/companies/{company_id}/complete-data` — one normalized employee-data contract merged from the source API and read-only database. Optional query parameters are `deputed_company_id` (`0` means all), `employee_code`, `updated_since`, `limit`, and `offset`. This supports polling for employees completed by HRMS onboarding without coupling the future service to the current UI.
- `POST /api/v1/companies/{id}/ingest` — add/update records in that company’s local inbox.
- `GET /api/v1/inbox?company={id}` — show incoming records.
- `POST /api/v1/validate` — validate selected inbox records before insertion.
- `POST /api/v1/schedules` — configure continuous API synchronization; schedules never insert automatically.
- `GET /api/v1/runs/{run_id}/handover` — return the manual handover bundle for every failed row.
- `POST /api/v1/runs/{run_id}/handover/acknowledge` — record that an operator reviewed the handover; it never retries or inserts rows.
- `POST /api/v1/runs/{run_id}/handover/email` — retry only the failed-run email after SMTP is configured; it never retries browser insertion.
- `GET /api/v1/runs/{run_id}/errors` — return the durable row-level error log for a run.
- `GET /api/v1/runs/{run_id}/evidence` — list audit logs, error logs, screenshots, and local proof files.
- `GET /api/v1/runs/{run_id}/evidence/{source}/{file}` — view one approved local proof/log file (`logs`, `artifacts`, or `blob`).
- `GET /api/v1/errors` — view the consolidated error list across local runs, with optional `run_id`, `company`, and `category` filters.

### Complete source-data API

The supplied `emanageadminapi-netcore` source has separate company, deputed-company, and PF-master endpoints. The local service keeps those integrations server-side and exposes one normalized contract for the frontend. `GET /api/v1/source/companies/{company_id}/complete-data` merges company/deputed-company scope, employee identity, PF UAN/status, Aadhaar, and Basic Wage. Every record includes `field_sources`, `missing_required_fields`, and `ready_for_pf_intake`. The six PF intake fields are `uan`, `aadhaar`, `name`, `dob`, `doj`, and `basic_wage`.

The database adapter uses only parameterized SELECT statements and requires `PF_READONLY_DB_CONNECTION`; it does not call the source database’s write procedures. The optional source API is configured with `EMANAGE_API_BASE_URL` and its server-side credentials. See `config/source_data.example.env` for variable names.

For local startup, create `config/source_data.env` from the example and fill the values locally. `start_local.ps1` loads that file only into the backend process environment; the file is ignored and must not be committed.

If any row or browser step fails, the run records a structured handover instead of silently skipping the problem. Handover categories include `PORTAL_DOWNTIME_OR_NETWORK`, `PORTAL_LAYOUT_CHANGE`, `DUPLICATE_UAN_OR_MEMBER`, `DATA_MISMATCH`, `VALIDATION_ERROR`, and `AUTOMATION_OR_AUTHENTICATION_ERROR`. The bundle contains the affected source rows, row-level messages, workflow step, and local evidence paths. Evidence is stored under `blob_storage/<run_id>/` and the bundle is written to `blob_storage/<run_id>/handover.json`; credentials are never included. Automatic retry is disabled so an operator can verify portal/member status before deciding what to do next. When SMTP settings are configured, the API run sends one email containing all affected rows and unique screenshots; without SMTP settings, the local handover remains the source of truth.

REAL requests must include `confirm_real: true`, employer credentials are loaded only for the selected scope and run, and JWT authentication protects the local API. The private MOCK harness is localhost-only and is not exposed in the user-facing portal. Each run is persisted under `api_runs/<run_id>/` with `run.json`, `audit.jsonl`, and browser evidence. REAL mode is separate and does not automate portal security checkpoints, and neither mode performs approval.

Production-style local safeguards include a `127.0.0.1`/`localhost` bind check, JSON body-size and content-type limits, atomic run-state writes, one active browser run at a time, safe-boundary stop handling, restart recovery that marks unfinished runs `RECOVERY_REQUIRED` instead of resubmitting them, and structured API errors. Login returns an `EPFO_JWT` HttpOnly cookie; API clients may send the same token as `Authorization: Bearer <token>`. Set `EPFO_JWT_SECRET` to a long random local secret so tokens survive service restarts. `EPFO_API_KEY` remains an optional integration fallback:

```powershell
$env:EPFO_JWT_SECRET = "use-a-long-random-local-secret"
$env:EPFO_API_KEY = "use-a-local-secret"
python backend/api_service.py
```

## Safety behavior

- The browser is visible.
- In REAL mode, CAPTCHA and any portal security checkpoint are completed manually by the operator; the automation pauses, records the checkpoint, and only verifies the resulting authenticated dashboard.
- The tool does not solve, crack, bypass, or automatically submit any real CAPTCHA or other portal security control.
- When REAL CAPTCHA is detected, a visible checkpoint screenshot is saved under the run artifacts and shown in the local monitor for the operator. The CAPTCHA value is never OCRed, extracted, logged, or submitted by automation.
- The tool never clicks **Approve** or **Reject**.
- Each row is checkpointed in `run_log.xlsx`; rerunning skips successful rows unless `--retry-failed` is selected.
- Portal passwords are encrypted at rest in the local credential database and are never included in logs, reports, evidence, or run state.

## Run

```powershell
python -m pip install -r backend/requirements.txt
python -m playwright install chromium
python backend/app.py
```

Use the window to select the workbook, enter the employer login ID/password, and click **Start**. The browser opens. Complete the real portal’s security checkpoints manually in the visible browser. The application polls for the resulting EPFO state and continues automatically after it verifies the authenticated dashboard; the Continue button only requests an immediate state check.

## Authentication state machine

The real mode uses the explicit states `LOGIN_PAGE`, `CAPTCHA_REQUIRED`, `WAITING_FOR_OPERATOR`, `AUTHENTICATION_VERIFY`, `AUTHENTICATED`, `AUTHENTICATION_FAILED`, `SESSION_EXPIRED`, `TIMEOUT`, and `REGISTRATION_READY`. Authentication stops at the manual CAPTCHA checkpoint until the operator completes it and submits the portal login form. Passwords are not written to logs, reports, screenshots, Excel, source code, or configuration.

Timeouts, polling, screenshots, safe recovery attempts, and authentication mode are configured in `config/settings.json`. Environment variables `EPFO_AUTH_MODE=REAL|MOCK` and `EPFO_MOCK_SCENARIO=SUCCESS|TIMEOUT|SESSION_EXPIRED|AUTHENTICATION_FAILURE` can override the settings for a run. MOCK mode is local-only and never contacts EPFO.

`samples/workbooks/sample_employee_data.xlsx` is a safe-format example containing dummy data. Generate another copy with:

```powershell
python tools/generators/create_sample_excel.py
```

`samples/workbooks/sample_validation_cases.xlsx` contains one valid row, one row with multiple invalid fields, and one duplicate UAN/Aadhaar row. It is intended to verify that bad rows are blocked before the browser inserts anything.

To run the fully unattended local mock authentication and Playwright regression suite:

```powershell
$env:EPFO_AUTH_MODE = "MOCK"
$env:EPFO_MOCK_SCENARIO = "SUCCESS"
$env:MOCK_EPFO_EMPLOYER_ID = "TEST_EMPLOYER"
$env:MOCK_EPFO_PASSWORD = "TEST_PASSWORD"
$env:MOCK_EPFO_CAPTCHA = "TEST_CAPTCHA"
pytest -q backend/tests
```

The mock server binds to `127.0.0.1` on an ephemeral port, contains only test authentication and registration controls, and is never used by REAL mode. CI uses this mode and never connects to EPFO.

Operational controls include `DRY_RUN=true` for validation-only simulation, `SINGLE_ROW=<Excel row or Sl. No>` for supervised canary work, and `BATCH_SIZE=<number>` for a sequential batch limit. The UI Pause and Stop controls take effect at safe row boundaries; Stop never starts a new row.

## Workbook columns

The importer accepts these headers (case/spacing/underscore differences are ignored):

`Sl. No`, `Emp Code`, `Company`, `Name`, `DOJ`, `DOB`, `Aadhar Number`, `Father Name`, `Marital Status`, `Husband/Wife Name`, `Gender`, `Mobile`, `Location`, `Basic Wage`, `UAN No`

All 15 columns above must exist in the workbook. Core employee values are mandatory; Husband/Wife Name is conditionally required when the marital status indicates a spouse relationship, so an unmarried employee may use a blank or `N/A` value.

## Pre-submission validation

Before any employee is entered into EPFO, the row is checked for required values, preserved-source whitespace, allowed character sets, integer and decimal data types, real dates and date relationships, future dates, exact UAN/Aadhaar/mobile formats, Aadhaar checksum, wage range rules, email format, case-insensitive supported gender and marital-status values, conditional spouse-name rules, duplicate UAN/Aadhaar values, and exact source-row/cell mapping. Values are not silently trimmed or corrected. All errors for a row are collected together; a row is submitted only when it has zero validation errors.

## Important

This is UI automation, not an EPFO API integration. Portal layout changes may require selector maintenance. Use a test establishment and masked/dummy data before production use.

## Maintainability

- Portal URLs and timeouts are in `config/settings.json`.
- Portal selectors and page contracts are centralized in `config/selectors.json`; the file is versioned with `schema_version`, `profile_name`, and `contracts` for login, dashboard, identity, registration, and result screens.
- Company-specific selector changes belong in `config/selector_profiles.json` and are selected by `selector_profile` in `config/company_configs.json`; update only the affected selector list or contract.
- Excel parsing and validation are in `data_processing/`.
- Reports and resume checkpoints are in `reporting/`.
- Browser workflow and evidence screenshots are in `epfo_bot.py`.
- Every failure records the Excel row, employee, error category, exact workflow step, message, and evidence path.
- Browser failures create a full-page screenshot named with the row and workflow step.
- Pre-submission Excel errors create an attachable evidence image.
- One failure email can include all failed rows and all unique screenshots.

If EPFO changes a field, button, or page layout, first update only the matching selector/profile configuration and `last_verified` metadata, then run the local validation tests. If a required control is absent, the tool fails closed, records `PORTAL_LAYOUT_CHANGE`, captures a screenshot, and creates a handover; it does not guess, silently skip the step, or click approval. Workflow code should be changed only when the business sequence itself changes.

## Failure email configuration

Email is disabled unless these environment variables are configured. The SMTP password is never stored in the workbook or source code.

```powershell
$env:EPFO_SMTP_HOST = "smtp.example.com"
$env:EPFO_SMTP_PORT = "587"
$env:EPFO_SMTP_USER = "automation@example.com"
$env:EPFO_SMTP_PASSWORD = "use-a-secret-from-your-password-manager"
$env:EPFO_EMAIL_FROM = "automation@example.com"
$env:EPFO_EMAIL_TO = "hr@example.com"
$env:EPFO_EMAIL_CC = "ops@example.com" # optional; comma or semicolon separated
$env:EPFO_SMTP_TLS = "1"
$env:EPFO_SMTP_SSL = "0" # use 1 for SMTPS servers such as port 465
python backend/app.py
```

The email body identifies every failed row and includes the employee, employee code, UAN, category, error type, workflow step, and message. Every unique local screenshot/evidence file found for the failed rows is attached. The run records `handover_email.sent=true` only after SMTP accepts the message; SMTP/configuration errors remain visible in the run state and logs. If email is not configured, no false delivery is reported and the same information remains in the handover JSON, run log, and artifact folder.
