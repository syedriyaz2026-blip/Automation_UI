"""Configurable ESI portal workflow.

The supplied ESIC archive contains an execution trace and payload contract,
but not the original Selenium source or selectors.  This adapter therefore
implements the verified workflow stages without guessing portal selectors.
Selectors are supplied by the caller/configuration and every stage fails
closed when its required control is unavailable.

CAPTCHA is deliberately a human checkpoint.  The worker fills credentials,
waits for the operator to complete CAPTCHA, then verifies the authenticated
state before continuing.
"""

from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


ESIC_PORTAL_URL = "https://portal.esic.gov.in/EmployerPortal/ESICInsurancePortal/Portal_Loginnew.aspx"

ARCHIVE_STAGES = (
    "Before Login",
    "After Login",
    "Duplicate Check",
    "Identity Details",
    "Address Details",
    "Nominee Details",
    "Bank Details",
    "Consent and Submission",
    "ESIC Number Retrieval",
    "Report and e-PehChan",
)


class EsiPortalWorkflowError(RuntimeError):
    """Raised when a configured ESI portal stage cannot be completed safely."""


def load_selector_profile(path: Path) -> dict[str, Any]:
    """Load a reviewed ESI selector profile and reject incomplete JSON."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EsiPortalWorkflowError(f"Unable to load ESI selector profile: {path}") from exc
    if not isinstance(payload, dict):
        raise EsiPortalWorkflowError("ESI selector profile must be a JSON object.")
    return payload


@dataclass
class EsiWorkflowResult:
    record_id: str
    status: str = "ESI_PENDING"
    current_stage: str = "Before Login"
    message: str = ""
    evidence: list[str] = field(default_factory=list)
    portal_reference: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "status": self.status,
            "current_stage": self.current_stage,
            "message": self.message,
            "evidence": list(self.evidence),
            "portal_reference": self.portal_reference,
        }


class EsiPortalWorkflow:
    """Run the archive-documented ESI sequence using a Playwright Page.

    ``selectors`` is intentionally explicit.  It must be populated from a
    reviewed ESIC portal selector profile; no PF selectors or guessed CSS are
    used.  Values may be a selector string or a list of fallback selectors.
    """

    def __init__(
        self,
        page: Any,
        *,
        selectors: dict[str, Any],
        artifact_dir: Path,
        captcha_timeout_seconds: int = 180,
        wait_for_captcha: Callable[[], None] | None = None,
    ) -> None:
        self.page = page
        self.selectors = selectors
        self.artifact_dir = Path(artifact_dir)
        self.captcha_timeout_seconds = max(30, int(captcha_timeout_seconds))
        self.wait_for_captcha = wait_for_captcha
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def _selector(self, key: str, *, required: bool = True) -> str | None:
        value = self.selectors.get(key)
        if isinstance(value, (list, tuple)):
            value = next((item for item in value if str(item).strip()), None)
        value = str(value).strip() if value else ""
        if not value and required:
            raise EsiPortalWorkflowError(f"Missing reviewed ESI selector: {key}")
        return value or None

    def _fill(self, key: str, value: Any) -> None:
        selector = self._selector(key)
        self.page.locator(selector).first.fill("" if value is None else str(value))

    def _click(self, key: str) -> None:
        selector = self._selector(key)
        self.page.locator(selector).first.click()

    def _fill_optional(self, key: str, value: Any) -> None:
        selector = self._selector(key, required=False)
        if selector and value not in (None, ""):
            self.page.locator(selector).first.fill(str(value))

    def _upload_optional(self, key: str, value: Any) -> None:
        selector = self._selector(key, required=False)
        if selector and value:
            self.page.locator(selector).first.set_input_files(str(value))

    def _screenshot(self, record_id: str, stage: str) -> str:
        safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in str(record_id))
        stage_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in stage)
        path = self.artifact_dir / f"esi_{safe}_{stage_name}.png"
        self.page.screenshot(path=str(path), full_page=True)
        return str(path)

    def _stage(self, result: EsiWorkflowResult, stage: str) -> None:
        result.current_stage = stage
        result.evidence.append(self._screenshot(result.record_id, stage))

    def login(self, username: str, password: str, result: EsiWorkflowResult) -> None:
        self.page.goto(str(self.selectors.get("portal_url") or ESIC_PORTAL_URL), wait_until="domcontentloaded")
        self._stage(result, "Before Login")
        self._fill("login_username", username)
        self._fill("login_password", password)
        self._stage(result, "CAPTCHA Checkpoint")

        # CAPTCHA is a security control and must be completed by the operator.
        # The optional callback can block on the application's UI checkpoint.
        if self.wait_for_captcha:
            self.wait_for_captcha()
        else:
            deadline = time.monotonic() + self.captcha_timeout_seconds
            captcha_selector = self._selector("captcha_input")
            while time.monotonic() < deadline:
                value = self.page.locator(captcha_selector).first.input_value()
                if value.strip():
                    break
                time.sleep(0.25)
            else:
                raise EsiPortalWorkflowError("CAPTCHA was not completed before the configured timeout.")

        self._click("login_submit")
        self.page.wait_for_load_state("domcontentloaded")
        dashboard = self._selector("authenticated_marker")
        if not self.page.locator(dashboard).first.is_visible():
            raise EsiPortalWorkflowError("ESI authentication could not be verified.")
        self._stage(result, "After Login")

    def process_record(self, record: dict[str, Any], *, username: str, password: str) -> EsiWorkflowResult:
        record_id = str(record.get("record_id") or record.get("employee_code") or record.get("row_number") or "unknown")
        result = EsiWorkflowResult(record_id=record_id)
        try:
            self.login(username, password, result)
            self._stage(result, "Duplicate Check")
            self._click("new_employee")
            self._stage(result, "Identity Details")
            for field in ("employee_code", "name", "dob", "gender", "doj", "aadhaar", "mobile", "father_name", "marital_status"):
                self._fill(field, record.get(field, ""))
            self._fill_optional("job_location", record.get("job_location", ""))
            self._fill_optional("esic_sub_code", record.get("esic_sub_code", ""))
            self._upload_optional("employee_photo", record.get("employee_photo", ""))
            self._upload_optional("identity_document", record.get("identity_document_path", ""))
            self._click("identity_next")

            self._stage(result, "Address Details")
            for field in (
                "present_address", "present_state", "present_district", "present_pincode",
                "permanent_address", "permanent_state", "permanent_district", "permanent_pincode",
            ):
                self._fill(field, record.get(field, ""))
            self._click("address_next")

            self._stage(result, "Nominee Details")
            for field in ("nominee_name", "nominee_relation", "nominee_address", "nominee_state", "nominee_district", "nominee_pincode"):
                self._fill(field, record.get(field, ""))
            self._click("nominee_next")

            self._stage(result, "Bank Details")
            for field in ("bank_account", "bank_ifsc", "bank_account_type"):
                self._fill(field, record.get(field, ""))
            self._click("bank_next")

            self._stage(result, "Consent and Submission")
            self._click("consent")
            self._click("submit")
            self.page.wait_for_load_state("domcontentloaded")

            result.portal_reference = self.page.locator(self._selector("portal_reference")).first.inner_text()
            success_marker = self._selector("success_marker")
            if not self.page.locator(success_marker).first.is_visible():
                raise EsiPortalWorkflowError("ESIC success marker was not found after submission.")
            self._stage(result, "ESIC Number Retrieval")
            self._stage(result, "Report and e-PehChan")
            result.status = "ESI_REGISTERED"
            result.message = "ESI registration result was submitted through the configured workflow."
            return result
        except Exception as exc:
            result.status = "ESI_FAILED"
            result.message = str(exc)
            try:
                result.evidence.append(self._screenshot(result.record_id, f"failure_{result.current_stage}"))
            except Exception:
                pass
            return result
