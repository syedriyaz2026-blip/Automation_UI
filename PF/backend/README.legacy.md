# PF backend boundary

PF-specific processing is implemented by the shared backend and
`backend/pf_automation/`. This folder is reserved for PF-only adapters and
contracts so the module can be extracted later without copying the API,
security, persistence, or evidence infrastructure.
