# EPFO Automation Architecture

## Scope

This is a local-only, API-first EPFO member-registration system. The reference recording is kept separately under reference/video/; it is not part of the runtime application.

## Current storage decision

The current local deployment intentionally uses no database. API inbox records, run state, audit JSONL, error logs, and evidence are stored as JSON/text/binary files under `api_inbox/`, `api_runs/`, and `blob_storage/` using atomic replacement where state is updated. `config/settings.json` declares this as `storage_backend: filesystem`.

The service boundaries are kept separate so a future database or blob provider can replace these stores later. That migration will be a planned storage-adapter change with schema/data migration and backup validation; it is not part of the current local implementation.

## Runtime flow

1. The operator signs into the local control panel.
2. A company profile is selected.
3. The source API places normalized records in that company’s local inbox.
4. The operator reviews the queue and runs validation.
5. Invalid rows are blocked and reported before browser automation.
6. The operator explicitly starts a run for valid rows.
7. Configuration and selector contracts are checked before the browser starts.
8. Playwright opens a visible EPFO browser session.
9. Real CAPTCHA is completed manually by the operator; the application does not implement any secondary-code workflow.
10. The worker verifies the authenticated dashboard.
11. Employees are processed sequentially, one row at a time.
12. UAN/Aadhaar identity verification and employment fields are entered.
13. The worker confirms Registered - Pending Approval.
14. The worker stops before Approve/Reject.
15. Errors create row-level logs, screenshots, handover JSON, and optional email notification.
16. The operator reviews and acknowledges handover; there is no automatic retry.

## Separation of responsibilities

- frontend/ — React/Vite local control surface only.
- backend/api_service.py — local HTTP API, run lifecycle, scheduling, and authentication.
- backend/epfo_bot.py — Playwright browser workflow and portal interaction.
- backend/config/ — company profiles, selector contracts, validation, and timeouts.
- backend/data_processing/ — normalization, validation, and duplicate detection.
- backend/handover.py and backend/reporting/ — evidence, error classification, and notifications.
- backend/api_runs/ — run state, audit log, error log, and browser artifacts.
- backend/blob_storage/ — local evidence/handover storage abstraction.
- reference/video/ — user-provided reference recording and extracted frames only.

## Change policy

Layout changes should first be handled in config/selectors.json or config/selector_profiles.json. A missing contract fails closed as PORTAL_LAYOUT_CHANGE. Workflow code is changed only when the business sequence changes. Every change must pass the Python test suite and the frontend production build.

## Reliability boundary

No browser technology can guarantee success against portal downtime, unknown layout changes, or security checkpoints. The system is designed to avoid unsafe submissions: it validates before entry, pauses for manual security, captures proof, and hands failures to an operator.
