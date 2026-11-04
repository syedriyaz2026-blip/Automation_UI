# PF UAT deployment

This package prepares the Platform/PF module for controlled UAT. It does not include credentials and does not deploy to an external server.

## UAT prerequisites

- Windows host with Python, Node.js, Microsoft ODBC Driver 18, and Chromium installed.
- Network access to SQL Server `10.0.2.20:1433`.
- A read-only SQL login for `Emanage_statutory`.
- A long random `EPFO_JWT_SECRET` and separate UAT `EPFO_API_KEY`.
- Company-scoped EPFO credentials stored through the protected configuration flow.
- A visible browser session for the human CAPTCHA checkpoint.

## Configure

Copy `Platform/backend/config/uat_pf.example.env` to a private environment file or set the variables in the UAT service account. Never commit the private file.

Run the preflight:

```powershell
$env:PF_READONLY_DB_CONNECTION = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=10.0.2.20;DATABASE=Emanage_statutory;UID=...;PWD=...;TrustServerCertificate=yes;ApplicationIntent=ReadOnly'
$env:EPFO_JWT_SECRET = '...'
powershell -ExecutionPolicy Bypass -File .\Platform\backend\uat_preflight.ps1
```

Keep `PF_EPFO_RUNS_ENABLED=false` until source data, credentials, and a small UAT canary have been reviewed. Enable it only for the approved UAT run.

## Start

```powershell
powershell -ExecutionPolicy Bypass -File .\Platform\backend\start_local.ps1
```

The API is available at `http://127.0.0.1:8792` and the UI at `http://127.0.0.1:5201` unless the UAT host maps them behind its approved reverse proxy.

For a machine-managed UAT session, run the supervisor under the approved Windows service or Task Scheduler account:

```powershell
powershell -ExecutionPolicy Bypass -File .\Platform\backend\run_uat_supervisor.ps1
```

It checks the API and UI every 30 seconds and restarts the local application if either process is unavailable. The background monitor performs source synchronization; set `PF_BACKGROUND_AUTO_PROCESS_ENABLED=true` only after UAT approval of automatic EPFO runs.

## UAT acceptance checks

1. `/health` reports `ok` and valid configuration.
2. Company list is read from SQL and is scope-correct.
3. Deputed-company list matches the selected parent company.
4. PF records contain source employee ID, employee code, name, UAN, Aadhaar, DOB, DOJ, and basic wage.
5. Missing/invalid fields remain visible and block processing.
6. Repeated fetches do not create duplicate successful records.
7. A changed source employee is marked for review rather than silently duplicated.
8. A failed record can be retried and retains its evidence.
9. A canary run stops for manual CAPTCHA and resumes only after login verification.
10. Reports and evidence are downloadable without exposing credentials.

The UI is a monitoring/control surface; the API, background monitor, and Playwright worker are the actual processing components. CAPTCHA solving remains manual.
