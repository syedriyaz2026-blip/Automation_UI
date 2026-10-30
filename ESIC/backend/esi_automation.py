"""ESI automation hand-off and evidence contract.

The supplied ESIC archive contains reports/assets and the payload contract but
no Java source or compiled worker.  This bridge makes that limitation explicit
and prevents the PF worker from being called for ESI records.  A future ESI
worker can consume this contract without changing intake, deduplication,
credentials, or reporting.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from Platform.backend.credential_vault import CredentialVault


ROOT = Path(__file__).resolve().parent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EsiAutomationBridge:
    def __init__(self, vault: CredentialVault):
        self.vault = vault

    def status(self) -> dict[str, Any]:
        command = os.getenv("ESI_AUTOMATION_COMMAND", "").strip()
        module_path = os.getenv("ESI_AUTOMATION_MODULE_PATH", "").strip()
        return {
            "module": "ESI",
            "available": bool(command or module_path),
            "worker_configured": bool(command or module_path),
            "command_configured": bool(command),
            "module_path_configured": bool(module_path),
            "captcha_policy": "Human confirmation is required at the portal CAPTCHA checkpoint.",
            "portal_url": "https://portal.esic.gov.in/EmployerPortal/ESICInsurancePortal/Portal_Loginnew.aspx",
            "checked_at": _now(),
        }

    def pending(
        self,
        *,
        company_id: str,
        deputed_company_id: str,
        total: int,
        valid: int,
        invalid: int,
        client_id: str = "",
        state: str = "",
        district: str = "",
    ) -> dict[str, Any]:
        account_configured = bool(client_id and self.vault.get_esi_account(client_id, state, district))
        worker = self.status()
        if invalid:
            reason = "VALIDATION_ERRORS_PRESENT"
        elif not account_configured:
            reason = "ESI_ACCOUNT_NOT_CONFIGURED"
        elif not worker["available"]:
            reason = "ESI_WORKER_NOT_CONFIGURED"
        else:
            reason = "READY_FOR_ESI_WORKER"
        return {
            "module": "ESI",
            "status": "PENDING_AUTOMATION",
            "reason_code": reason,
            "company_id": str(company_id),
            "deputed_company_id": str(deputed_company_id),
            "client_id": str(client_id),
            "total": int(total),
            "valid": int(valid),
            "invalid": int(invalid),
            "account_configured": account_configured,
            "worker": worker,
            "message": (
                "Correct the validation errors before automation."
                if invalid
                else "ESI records are stored in the selected scope and are awaiting the configured ESI workflow worker. The PF worker was not invoked."
            ),
            "captcha_policy": worker["captcha_policy"],
            "evidence_scope": str((ROOT / "evidence" / "ESI" / str(company_id) / str(deputed_company_id)).resolve()),
        }


ESI_AUTOMATION = EsiAutomationBridge(CredentialVault(ROOT / "config" / "credential_vault.json"))

