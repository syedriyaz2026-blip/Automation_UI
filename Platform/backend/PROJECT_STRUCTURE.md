# Project structure

The application is intentionally split into a browser UI and a local backend. Runtime state stays inside `backend/api_runs`; source code, configuration, tests, and sample inputs remain separate from generated evidence.

```text
PF_auto_f/
├── README.md
├── modules/                    # module ownership boundaries and manifests
│   ├── MODULE_REGISTRY.json    # PF, ESI, LWF, and PT lifecycle registry
│   ├── PF/{frontend,backend}/   # PF-specific contracts and adapters
│   ├── ESIC/{frontend,backend}/ # ESI-specific contracts and adapters
│   ├── LWF/{frontend,backend}/  # reserved LWF module boundary
│   └── PT/{frontend,backend}/   # reserved PT module boundary
├── frontend/
│   ├── src/                    # React/Vite operator interface
│   ├── package.json
│   └── vite.config.js
└── backend/
    ├── api_service.py          # Local HTTP API and lifecycle
    ├── platform_backend.py     # Intake, validation, run state, evidence
    ├── source_data.py          # Read-only company/employee source adapters
    ├── epfo_bot.py             # Playwright workflow
    ├── automation/             # Browser automation helpers
    ├── portal_pages/            # Portal page objects
    ├── config/                  # Selectors, settings, company mappings
    ├── data_processing/         # Excel parsing and validation
    ├── reporting/               # Audit logs and handover reporting
    ├── docs/                    # Workflow and integration documentation
    ├── samples/                 # Safe sample data and workbooks
    ├── tests/                   # Backend and mock-browser tests
    ├── tools/                   # Generators and one-off utilities
    ├── api_runs/                # Generated uploads, run state, logs, evidence
    ├── start_local.ps1
    └── stop_local.ps1
```

The `modules/` folders are intentionally lightweight ownership boundaries;
shared security, navigation, persistence, evidence, and API composition stay
in the root `frontend/` and `backend/` runtimes to avoid duplicated or
conflicting implementations.

From `PF_auto_f`, run `python -m pytest -q backend/tests`, `npm --prefix frontend run build`, or `./backend/start_local.ps1`.
