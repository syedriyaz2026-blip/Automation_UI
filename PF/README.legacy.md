# PF module

PF is the active Provident Fund module. Its module-specific responsibilities
are employee intake, mandatory-field normalization and validation, duplicate
protection, scoped credentials, evidence, reporting, and supervised EPFO
automation.

The shared runtime composes this module through the `PF` module key. Backend
automation remains under `backend/pf_automation/`; the local module backend
folder is the ownership/documentation boundary for PF contracts and future
adapters.
