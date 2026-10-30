# PF Automation Workflow

This folder contains the local statutory operations application. The active
modules are organized under [`modules`](modules): PF and ESIC/ESI are active,
while LWF and PT are registered as under construction. The shared runtime is
kept in separate top-level [`frontend`](frontend) and [`backend`](backend)
folders so authentication, permissions, database storage, evidence, and audit
behavior cannot drift between modules.

## Workflow

1. Authenticate to the local statutory operations control panel.
2. The backend returns the signed role, company assignments, permissions, and
   statutory module grants.
3. The Master Admin can create Super Admin accounts and choose which modules
   each Super Admin may access. Backend checks enforce the same grants even if
   a caller bypasses the UI.
4. Select an authorized module, Company, and Deputed Company scope.
5. Receive data through the authorized API or validated Excel intake path.
6. Normalize and validate records, show all source/normalized fields, reject
   invalid mandatory rows, and deduplicate repeated submissions.
7. Store timestamped status, audit events, evidence, and report details by
   module and exact company scope.
8. Run only the module's supervised automation workflow for valid records.

The workflow never approves or rejects a member, and it does not automatically
solve or submit a real CAPTCHA. Real portal security checkpoints remain
supervised.

For the Master Admin account and API details, see
[`backend/docs/MASTER_ADMIN.md`](backend/docs/MASTER_ADMIN.md).

## Run locally

Backend:

```powershell
cd automation\PF_auto_f\backend
python api_service.py
```

Frontend:

```powershell
cd automation\PF_auto_f\frontend
npm install
npm run dev
```

The original backend and frontend remain separated so the browser automation stays isolated from the UI.
