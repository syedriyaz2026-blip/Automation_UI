# EPFO Member Registration Automation — Production Readiness Report

Date: 05 October 2026

## Executive status

The repository now has a tested, isolated MOCK authentication environment and a safer sequential registration workflow. The MOCK path is suitable for development, CI, regression testing, and demonstrations. REAL EPFO operation remains supervised and is not certified for unattended production until selector health and a single-row canary are completed against the current EPFO portal.

## Repository audit

| Area | Existing implementation | Current status |
|---|---|---|
| Entry point | `app.py` Tkinter UI | Preserved and extended |
| Browser | Playwright visible browser | REAL visible; MOCK headless in tests |
| Authentication | `epfo_bot.py` login checkpoint | Explicit REAL state machine plus isolated MOCK server |
| Excel | `excel_reader.py`, `epfo_bot.read_records` | Exact 15-column contract enforced |
| Validation | `data_processing/validator.py` | Multi-error row validation preserved |
| Duplicates | `data_processing/duplicate_checker.py` | Duplicate UAN/Aadhaar protection preserved |
| Reporting | `reporting/reporting.py` | Run report and Pending Approval checkpoint preserved |
| Evidence/email | `reporting/email_reporter.py`, screenshots | Preserved and extended for auth/save failures |
| Tests | `tests/` | Unit, state-detection, Playwright mock, and E2E coverage added |

No files were deleted. No database or external storage dependency was introduced.

## Architecture before and after

Before:

`Tkinter → Excel validation → visible Playwright → fill login → manual Continue → registration → report`

After:

`Tkinter → preflight → workbook/data quality → duplicate protection → authentication manager behavior → dashboard verification → registration state checks → Pending Approval hard stop → report/audit/checkpoint`

Authentication is separated by mode:

- `REAL`: configured EPFO URL, runtime password, manual CAPTCHA, actual dashboard verification.
- `MOCK`: dynamically created `127.0.0.1` server, deterministic CAPTCHA, Playwright-driven automated tests, no EPFO connection.

## Implemented changes

- `authentication.py`: explicit states, mode guard, mock scenarios, result model.
- `mock_epfo_server.py`: isolated local login, CAPTCHA, dashboard, identity, and registration pages.
- `epfo_bot.py`: state detection, masked auth evidence, identity confirmation, Pending Approval confirmation, unknown-save protection.
- `preflight.py`: mode, workbook, artifact, browser, selector, and endpoint safety checks.
- `data_processing/data_quality.py`: total/valid/invalid/duplicate counts and issue distribution workbook.
- `reporting/audit_log.py`: append-oriented JSONL run events.
- `reporting/reporting.py`: preserves successful checkpoints and blocks blind retry of unknown Save results.
- `app.py`: run IDs, preflight, quality report, audit events, DRY_RUN, SINGLE_ROW, BATCH_SIZE, Pause, and Stop.
- `.github/workflows/tests.yml`: CI uses MOCK mode only.

## Security boundaries

REAL mode never solves CAPTCHA and never implements a secondary-code workflow. Passwords are not written to source, Excel, logs, reports, or unmasked screenshots. Endpoint guards reject ambiguous modes. The codebase has no Approve, Reject, DSC, eSign, or final-approval action.

The registration success path returns only after `Registered - Pending Approval` is detected. An uncertain Save result is recorded as `Save Result Unknown` and is not automatically resubmitted on resume.

## Data contract and validation

The approved `EPFO_MEMBER_V1` contract contains exactly the 15 documented columns. Unsupported columns and missing headers are rejected; original Excel row numbers are preserved. Existing validation covers required values, dates, Aadhaar Verhoeff checksum, UAN, mobile, wage, gender, marital status, spouse condition, and duplicate protection.

## Test results

```text
python -m py_compile app.py epfo_bot.py authentication.py mock_epfo_server.py preflight.py
pytest -q
15 passed
```

Coverage includes login/CAPTCHA/dashboard state detection, mock CAPTCHA failure, timeout, session expiry, authentication failure, full mock registration, Pending Approval confirmation, report checkpoint behavior, and sample workbook validation.

Sample data check:

```text
samples/workbooks/sample_employee_data.xlsx: 2 rows, 2 valid, 0 invalid, 0 duplicates
```

## Configuration and deployment

Install dependencies and browser:

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

CI/development uses:

```powershell
$env:EPFO_AUTH_MODE = "MOCK"
$env:EPFO_MOCK_SCENARIO = "SUCCESS"
pytest -q
```

REAL supervised operation uses `EPFO_AUTH_MODE=REAL`, the configured EPFO URL, runtime employer credentials, and manual CAPTCHA. Run a single-row canary before a batch.

## Rollback

Because changes are file-based and no schema migration or database was added, rollback is a source-control revert of the changed files. Preserve generated run logs, audit JSONL, evidence, and quality reports for audit before removing any artifacts.

## Remaining production gates

These items require organizational or live-portal validation and are not honestly certifiable from a local mock:

1. Supervised REAL single-row test against the current EPFO selectors.
2. Selector health test against the current portal.
3. Portal maintenance/error-page checks using the current live layout.
4. Organization-approved artifact access control and retention enforcement.
5. Browser-crash recovery and machine-restart recovery drill.
6. SMTP delivery test and operational alert routing.
7. Canary review by an authorized operator before larger batches.

Until those gates pass, use MOCK for automated testing and REAL only under supervised operation. The approval boundary remains permanently manual.
