# Automation_UI migration plan

## Ownership

- `Platform/frontend`: the runnable shared React/Vite shell and design system.
- `Platform/backend`: API server, authentication, JWT/RBAC, common storage,
  company scope, audit, evidence, reports, validation utilities, and tests.
- `PF/backend`: PF portal configuration, Playwright pages, PF bot, CAPTCHA
  adapter, PF test harness, and PF workbook templates.
- `ESIC/backend`: ESIC source contract, conversion/validation, store, report,
  and automation bridge.
- `LWF` and `PT`: reserved frontend/backend module boundaries, disabled until
  their independent statutory implementations are supplied.

## Data safety

The source tree was backed up before migration to:
`C:\Users\RiyazBashaSyed\Downloads\Automation_UI_migration_backup_20261029`.
Existing SQLite databases, JSON configuration, API runs, uploads, logs, and
workbooks are copied into the target. No database reset or seed replacement is
performed by this migration.

## Compatibility

The shared backend keeps small import shims for legacy flat-module callers.
Production imports use package-qualified module paths after the migration.
