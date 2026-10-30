from __future__ import annotations

import re
import datetime as dt
import time
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from openpyxl import load_workbook
from playwright.sync_api import TimeoutError as SeleniumTimeoutError
from Platform.backend.automation.playwright_page import PlaywrightPage as Page
from Platform.backend.authentication import AuthState, AuthenticationResult, MockAuthenticationProvider, auth_mode, ensure_auth_environment, mock_scenario
from PF.backend.portal_config import PortalConfig, PortalConfigurationError
from PF.backend.captcha_service import CaptchaDetector, CaptchaStatus
from PF.backend.captcha_solver import CaptchaReadError, read_captcha
from PF.backend.portal_pages.errors import PortalLayoutChange
from PF.backend.portal_pages.authentication_page import AuthenticationPage
from PF.backend.portal_pages.identity_page import IdentityPage
from PF.backend.portal_pages.employment_page import EmploymentPage
from PF.backend.portal_pages.result_page import ResultPage


LogFn = Callable[[str], None]


ALIASES = {
    "sl_no": ["sl no", "sl. no", "serial no", "serial number"],
    "emp_code": ["emp code", "employee code", "empcode"],
    "company": ["company", "company name"],
    "name": ["name", "employee name", "employee"],
    "doj": ["doj", "date of joining", "joining date"],
    "dob": ["dob", "date of birth", "birth date"],
    "aadhaar": ["aadhaar number", "aadhar number", "aadhaar", "aadhar"],
    "father_name": ["father name", "father's name", "father/husband name"],
    "marital_status": ["marital status", "marital"],
    "husband_wife_name": ["husband/wife name", "husband wife name", "spouse name"],
    "basic_wage": ["basic wage", "basic wages", "monthly epf wages", "wage"],
    "uan": ["uan no", "uan", "uan number", "previous uan"],
    "mobile": ["mobile", "mobile no", "mobile number"],
    "email": ["email", "email id", "email address"],
    "location": ["location", "city", "work location"],
    "gender": ["gender"],
}


def norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def excel_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def read_records(path: Path) -> list[dict[str, str]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        raise ValueError("The workbook is empty.")
    header_map = {norm(v): i for i, v in enumerate(rows[0]) if v is not None}
    allowed_headers = {norm(alias) for aliases in ALIASES.values() for alias in aliases}
    unsupported = [str(value) for value in rows[0] if value is not None and norm(value) not in allowed_headers]
    if unsupported:
        raise ValueError("Unsupported columns found; use only the approved 15-column format: " + ", ".join(unsupported))
    resolved: dict[str, int] = {}
    for key, aliases in ALIASES.items():
        for alias in aliases:
            if norm(alias) in header_map:
                resolved[key] = header_map[norm(alias)]
                break
    # The intake contract requires only the six PF creation fields. All other
    # supported columns remain optional enrichment and are passed through when
    # present in the workbook.
    required = ["name", "doj", "dob", "aadhaar", "basic_wage", "uan"]
    missing = [x for x in required if x not in resolved]
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))
    records = []
    for row_number, row in enumerate(rows[1:], start=2):
        if not any(v is not None and str(v).strip() for v in row):
            continue
        item = {key: excel_value(row[index] if index < len(row) else "") for key, index in resolved.items()}
        item["row_number"] = str(row_number)
        item["status"] = "Queued"
        records.append(item)
    if not records:
        raise ValueError("No employee records found below the header row.")
    return records


def validate_record(record: dict[str, str]) -> Optional[str]:
    digits_aadhaar = re.sub(r"\D", "", record.get("aadhaar", ""))
    digits_uan = re.sub(r"\D", "", record.get("uan", ""))
    if len(digits_aadhaar) not in (12, 16):
        return "Aadhaar must contain 12 digits (or a 16-digit enrolment number)."
    if len(digits_uan) != 12:
        return "UAN must contain 12 digits."
    for key in ("name", "doj", "dob", "basic_wage"):
        if not record.get(key, "").strip():
            return f"Missing {key}."
    return None


@dataclass
class RegistrationResult:
    row_number: str
    name: str
    status: str
    message: str
    activity_id: str = ""
    error_type: str = ""
    workflow_step: str = ""
    screenshot: str = ""


class EpfoBot:
    def __init__(self, page: Page, log: LogFn, artifact_dir: Path, timeout_ms: int = 25000, selector_profile: str = "", company_name: str = "", deputed_company_name: str = ""):
        self.page = page
        self.log = log
        self.artifact_dir = artifact_dir
        self.company_name = company_name or "UnknownCompany"
        self.deputed_company_name = deputed_company_name or "UnknownDeputedCompany"
        self.timeout_ms = timeout_ms
        self.portal_config = PortalConfig(profile=selector_profile)
        self.settings = self.portal_config.settings
        self.selectors = self.portal_config.selectors
        self.selector_profile = self.portal_config.profile or "EPFO_EMPLOYER_DEFAULT"
        self.current_step = "Unstarted"
        self.mock_base_url = ""
        self.last_auth_screenshot = ""
        self.captcha = CaptchaDetector(
            self.page,
            lambda: self._auth_selectors("captcha", []),
            self.artifact_dir,
            bool(self.settings.get("screenshot_on_failure", True)),
        )
        self.last_captcha_observation = self.captcha.last_observation
        # Section page objects keep UI changes localized; this class remains the
        # sequential orchestration boundary for run state and handover.
        self.authentication_page = AuthenticationPage(self.page, self.portal_config, self.artifact_dir, self.log, self.settings)
        self.identity_page = IdentityPage(self.page, self.portal_config, self.artifact_dir, self.log)
        self.employment_page = EmploymentPage(self.page, self.portal_config, self.artifact_dir, self.log)
        self.result_page = ResultPage(self.page, self.portal_config, self.artifact_dir, self.log)

    def capture_evidence(self, stage: str, outcome: str = "in_progress") -> str:
        """Save an auditable stage screenshot under company/deputed-company evidence folders."""
        safe = lambda text: re.sub(r"[^A-Za-z0-9_-]+", "_", str(text)).strip("_") or "Unknown"
        stamp = dt.datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        bucket = "Success Evidence" if outcome.lower() == "success" else "Failure Evidence" if outcome.lower() == "failure" else stage
        folder = self.artifact_dir.parent.parent.parent / "evidence" / safe(self.company_name) / safe(self.deputed_company_name) / safe(bucket)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{safe(self.company_name)}_{safe(self.deputed_company_name)}_PF_{safe(stage)}_{stamp}.png"
        self.page.screenshot(path=str(target), full_page=True)
        self.log(f"Evidence captured: {target}")
        return str(target)

    def set_selector_profile(self, profile: str) -> None:
        """Apply company-specific selector overrides without changing workflow code."""
        try:
            self.selectors = self.portal_config.with_profile(profile)
            self.selector_profile = profile
        except PortalConfigurationError as exc:
            raise PortalLayoutChange(f"PORTAL_LAYOUT_CHANGE: {exc}") from exc

    def _selectors(self, key: str, fallback: list[str] | None = None) -> list[str]:
        # Keep lightweight unit-test doubles and older integrations compatible
        # when they construct the worker without running __init__.
        if not hasattr(self, "portal_config"):
            values = self.selectors.get(key)
            if isinstance(values, list) and values:
                return values
            if fallback and self.settings.get("allow_code_selector_fallback", False):
                return fallback
            raise PortalLayoutChange(f"PORTAL_LAYOUT_CHANGE: selector key '{key}' is missing or empty in config/selectors.json.")
        try:
            return self.portal_config.selectors_for(key, fallback)
        except PortalConfigurationError as exc:
            raise PortalLayoutChange(f"PORTAL_LAYOUT_CHANGE: {exc}") from exc

    def _contract(self, name: str) -> list[str]:
        if not hasattr(self, "portal_config"):
            contracts = self.selectors.get("contracts", {})
            keys = contracts.get(name) if isinstance(contracts, dict) else None
            if not isinstance(keys, list) or not keys:
                raise PortalLayoutChange(f"PORTAL_LAYOUT_CHANGE: selector contract '{name}' is missing from config/selectors.json.")
            return [str(key) for key in keys]
        try:
            return self.portal_config.contract(name)
        except PortalConfigurationError as exc:
            raise PortalLayoutChange(f"PORTAL_LAYOUT_CHANGE: {exc}") from exc

    def _save_layout_screenshot(self, contract: str) -> str:
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            target = self.artifact_dir / f"layout_change_{re.sub(r'[^A-Za-z0-9_-]+', '_', contract)}.png"
            self.page.screenshot(path=str(target), full_page=True)
            return str(target)
        except Exception:
            return ""

    def _assert_contract(self, contract: str) -> None:
        missing_config = [key for key in self._contract(contract) if not isinstance(self.selectors.get(key), list) or not self.selectors.get(key)]
        if missing_config:
            raise PortalLayoutChange(f"PORTAL_LAYOUT_CHANGE: selector configuration is missing {', '.join(missing_config)}.")
        missing_page = [key for key in self._contract(contract) if not self._visible(self.selectors[key])]
        if missing_page:
            screenshot = self._save_layout_screenshot(contract)
            evidence = f" Evidence: {screenshot}" if screenshot else ""
            raise PortalLayoutChange(f"PORTAL_LAYOUT_CHANGE: page contract '{contract}' is not satisfied; missing visible controls: {', '.join(missing_page)}.{evidence}", screenshot)

    def _first(self, selectors: list[str]):
        last = None
        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                locator.wait_for(state="visible", timeout=2500)
                return locator
            except Exception as exc:
                last = exc
        raise PortalLayoutChange(f"PORTAL_LAYOUT_CHANGE: no configured selector matched the current page ({', '.join(selectors)}).")

    def _fill(self, selectors: list[str], value: str):
        self._first(selectors).fill(value)
        self._demo_wait()

    def _type_verified(self, selectors: list[str], value: str):
        """Use keyboard events and confirm the portal retained the entered value."""
        locator = self._first(selectors)
        locator.click()
        locator.type(value, delay=50)
        if locator.input_value() != value:
            raise PortalLayoutChange("PORTAL_LAYOUT_CHANGE: login field did not retain the entered value.")

    def _click(self, selectors: list[str]):
        self._first(selectors).click()
        self._demo_wait()

    def _demo_wait(self):
        if auth_mode(self.settings) == "MOCK":
            delay = int(self.settings.get("mock_demo_delay_ms", 0) or 0)
            if delay:
                time.sleep(delay / 1000)

    def _type_demo(self, selectors: list[str], value: str):
        locator = self._first(selectors)
        locator.fill("")
        delay = int(self.settings.get("mock_typing_delay_ms", 0) or 0)
        if delay:
            locator.type(value, delay=delay)
        else:
            locator.fill(value)
        self._demo_wait()

    def _wait_for_any(self, selectors: list[str], timeout_ms: int) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if self._visible(selectors):
                return True
            time.sleep(0.25)
        return False

    def _page_text(self) -> str:
        try:
            return self.page.locator("body").inner_text(timeout=3000).lower()
        except Exception:
            return ""

    def _visible(self, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                if self.page.locator(selector).first.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    def _auth_selectors(self, key: str, fallback: list[str]) -> list[str]:
        return self._selectors(key, fallback)

    def _detect_auth_state(self) -> AuthState:
        text = self._page_text()
        if self._visible(self._auth_selectors("session_expired", [])) or any(x in text for x in ("session expired", "your session has expired")):
            return AuthState.SESSION_EXPIRED
        if self._visible(self._auth_selectors("auth_failure", [])) or any(x in text for x in ("authentication failed", "invalid credentials")):
            return AuthState.AUTHENTICATION_FAILED
        if self._visible(self._auth_selectors("auth_success", [])):
            return AuthState.AUTHENTICATED
        captcha_observation = self._observe_captcha(capture=False)
        if captcha_observation.status in (CaptchaStatus.PRESENT, CaptchaStatus.CHANGED) or any(x in text for x in ("enter captcha", "captcha code")):
            return AuthState.CAPTCHA_REQUIRED
        if self._visible(self._auth_selectors("login_password", [])):
            return AuthState.LOGIN_PAGE
        if self._visible(self._auth_selectors("registration_ready", [])):
            return AuthState.REGISTRATION_READY
        return AuthState.UNEXPECTED_PORTAL_STATE

    def _observe_captcha(self, capture: bool = True):
        """Observe CAPTCHA safely; compatibility fallback keeps old unit doubles working."""
        if not hasattr(self, "captcha"):
            self.captcha = CaptchaDetector(
                self.page,
                lambda: self._auth_selectors("captcha", []),
                getattr(self, "artifact_dir", Path("reports")),
                bool(getattr(self, "settings", {}).get("screenshot_on_failure", True)),
            )
        self.last_captcha_observation = self.captcha.observe(capture=capture)
        return self.last_captcha_observation

    def _save_auth_screenshot(self, state: AuthState) -> str:
        if not self.settings.get("screenshot_on_failure", True):
            return ""
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            # Hide secrets in the rendered page without reading or logging their values.
            self.page.add_style_tag(content="input[type='password'], input[name='captcha'], #captcha { color: transparent !important; text-shadow: none !important; }")
            target = self.artifact_dir / f"authentication_{state.value.lower()}.png"
            self.page.screenshot(path=str(target), full_page=True)
            self.last_auth_screenshot = str(target)
            if state not in {AuthState.CAPTCHA_REQUIRED}:
                self.capture_evidence("Before Login" if state in {AuthState.AUTHENTICATION_FAILED, AuthState.TIMEOUT, AuthState.UNEXPECTED_PORTAL_STATE} else "After Login", "failure")
            return self.last_auth_screenshot
        except Exception:
            return ""

    def authenticate(self, on_state: Optional[Callable[[AuthState, str], None]] = None, continue_event=None) -> AuthenticationResult:
        """Fill the displayed CAPTCHA, submit sign in, and verify the dashboard.

        Authentication is intentionally limited to employer ID, password, CAPTCHA,
        and dashboard verification.
        """
        mode = auth_mode(self.settings)
        ensure_auth_environment(self.settings, mode)
        if mode == "MOCK":
            return self._authenticate_mock_browser(on_state)
        if mode != "REAL":
            return AuthenticationResult(AuthState.AUTHENTICATION_FAILED, f"Unsupported authentication mode: {mode}", "Configuration Error")

        def transition(state: AuthState, message: str):
            self.log(f"Authentication state: {state.value} — {message}")
            if on_state:
                on_state(state, message)
            self._demo_wait()

        auth_deadline = time.monotonic() + int(self.settings.get("authentication_timeout_ms", 180000)) / 1000
        captcha_deadline = None
        last_state = None
        attempted_fingerprints: set[str] = set()
        max_attempts = max(1, int(self.settings.get("max_captcha_attempts", 3)))
        interval = max(0.2, int(self.settings.get("polling_interval_ms", 1000)) / 1000)

        while time.monotonic() < auth_deadline:
            state = self._detect_auth_state()
            if state != last_state:
                last_state = state
                if state == AuthState.CAPTCHA_REQUIRED:
                    captcha_deadline = time.monotonic() + int(self.settings.get("captcha_timeout_ms", 180000)) / 1000
                    transition(state, "CAPTCHA detected; reading the displayed image.")
                elif state == AuthState.AUTHENTICATED:
                    self._assert_contract("authenticated_dashboard")
                    transition(AuthState.AUTHENTICATION_VERIFY, "Authenticated controls detected; verifying the expected dashboard.")
                    if not self._visible(self._auth_selectors("auth_success", [])):
                        return AuthenticationResult(AuthState.UNEXPECTED_PORTAL_STATE, "Authenticated dashboard controls could not be verified.", "Unexpected Portal State", self._save_auth_screenshot(AuthState.UNEXPECTED_PORTAL_STATE))
                    self._dismiss_post_login_alert()
                    transition(AuthState.AUTHENTICATED, "Authenticated EPFO dashboard confirmed.")
                    self.capture_evidence("After Login")
                    transition(AuthState.REGISTRATION_READY, "Authentication checkpoint complete; registration may continue.")
                    return AuthenticationResult(AuthState.REGISTRATION_READY, "Automated sign in succeeded.")
                elif state == AuthState.AUTHENTICATION_FAILED:
                    error_type, message = self._classify_auth_failure(self._page_text())
                    return AuthenticationResult(state, message, error_type, self._save_auth_screenshot(state))
                elif state == AuthState.SESSION_EXPIRED:
                    return AuthenticationResult(state, "EPFO session expired; run stopped safely.", "Session Expired", self._save_auth_screenshot(state))
                elif state == AuthState.UNEXPECTED_PORTAL_STATE:
                    transition(state, "Expected login, CAPTCHA, or dashboard state was not found.")

            if state == AuthState.CAPTCHA_REQUIRED:
                try:
                    image_bytes, fingerprint = self.captcha.image_bytes()
                except Exception:
                    return AuthenticationResult(AuthState.AUTHENTICATION_FAILED, "CAPTCHA image could not be captured.", "CAPTCHA_READ_FAILED", self._save_auth_screenshot(AuthState.AUTHENTICATION_FAILED))
                if fingerprint not in attempted_fingerprints:
                    if len(attempted_fingerprints) >= max_attempts:
                        return AuthenticationResult(AuthState.AUTHENTICATION_FAILED, "CAPTCHA was rejected after the configured number of attempts.", "CAPTCHA_INVALID", self._save_auth_screenshot(AuthState.AUTHENTICATION_FAILED))
                    try:
                        answer = read_captcha(image_bytes)
                    except CaptchaReadError as exc:
                        return AuthenticationResult(AuthState.AUTHENTICATION_FAILED, str(exc), "CAPTCHA_READ_FAILED", self._save_auth_screenshot(AuthState.AUTHENTICATION_FAILED))
                    transition(AuthState.CAPTCHA_PROCESSING, f"Filling CAPTCHA and clicking Sign In (attempt {len(attempted_fingerprints) + 1} of {max_attempts}).")
                    try:
                        self._type_verified(self._auth_selectors("captcha_input", []), answer)
                        if self.captcha.image_bytes()[1] != fingerprint:
                            transition(AuthState.CAPTCHA_REQUIRED, "CAPTCHA image changed during entry; reading the new image.")
                            continue
                        if not self._first(self._auth_selectors("login_username", [])).input_value() or not self._first(self._auth_selectors("login_password", [])).input_value():
                            raise PortalLayoutChange("PORTAL_LAYOUT_CHANGE: login credentials are empty before Sign In.")
                        attempted_fingerprints.add(fingerprint)
                        self._first(self._auth_selectors("login_submit", [])).click(timeout=int(self.settings.get("navigation_timeout_ms", 25000)))
                    except Exception:
                        return AuthenticationResult(AuthState.AUTHENTICATION_FAILED, "CAPTCHA field or Sign In button could not be used.", "PORTAL_LAYOUT_CHANGE", self._save_auth_screenshot(AuthState.AUTHENTICATION_FAILED))
                    # Never log, persist, or return the recognized characters.
                    del answer
                elif "invalid captcha" in self._page_text():
                    return AuthenticationResult(AuthState.AUTHENTICATION_FAILED, "EPFO rejected the CAPTCHA and did not provide a new challenge.", "CAPTCHA_INVALID", self._save_auth_screenshot(AuthState.AUTHENTICATION_FAILED))

            if state == AuthState.CAPTCHA_REQUIRED and captcha_deadline and time.monotonic() > captcha_deadline:
                self.captcha.mark_expired()
                return AuthenticationResult(AuthState.TIMEOUT, "CAPTCHA checkpoint timed out; run stopped safely.", "CAPTCHA Timeout", self._save_auth_screenshot(AuthState.TIMEOUT))
            operator_signal = continue_event is not None and continue_event.is_set()
            if operator_signal:
                continue_event.clear()
                self.log("Immediate authentication-state check requested.")
            time.sleep(interval)

        return AuthenticationResult(AuthState.TIMEOUT, "Authentication timed out; run stopped safely.", "Authentication Timeout", self._save_auth_screenshot(AuthState.TIMEOUT))

    def _authenticate_mock_browser(self, on_state: Optional[Callable[[AuthState, str], None]]) -> AuthenticationResult:
        """Drive the isolated localhost mock page through Selenium."""
        def transition(state: AuthState, message: str):
            self.log(f"Authentication state: {state.value} — {message}")
            if on_state:
                on_state(state, message)
            self._demo_wait()

        scenario = mock_scenario(self.settings)
        username = os.getenv("MOCK_EPFO_EMPLOYER_ID", "TEST_EMPLOYER")
        password = os.getenv("MOCK_EPFO_PASSWORD", "TEST_PASSWORD")
        captcha = os.getenv("MOCK_EPFO_CAPTCHA", "")
        try:
            transition(AuthState.LOGIN_PAGE, "Local mock login page detected.")
            self._type_demo(self._auth_selectors("login_username", ["input[name='username']"]), username)
            self._type_demo(self._auth_selectors("login_password", ["input[name='password']"]), password)
            transition(AuthState.CAPTCHA_REQUIRED, "Mock CAPTCHA checkpoint detected.")
            transition(AuthState.CAPTCHA_PROCESSING, "Submitting deterministic mock CAPTCHA.")
            # The local mock renders the answer as text for deterministic tests.
            if self._visible([".captcha-image"]):
                captcha = self.page.locator(".captcha-image").inner_text().strip()
            self._type_demo(self._auth_selectors("captcha_input", ["input[name='captcha']"]), captcha)
            self._click(self._auth_selectors("login_submit", ["button[type='submit']"]))
            if scenario in {"CAPTCHA_INVALID", "CAPTCHA_TIMEOUT"}:
                message = "Mock CAPTCHA failed or timed out."
                self.capture_evidence("Login", "failure")
                return AuthenticationResult(AuthState.AUTHENTICATION_FAILED, message, "CAPTCHA_INVALID", self._save_auth_screenshot(AuthState.AUTHENTICATION_FAILED))
            transition(AuthState.CAPTCHA_VALID, "Mock CAPTCHA accepted.")
            if "session expired" in self._page_text().lower():
                self.capture_evidence("Login", "failure")
                return AuthenticationResult(AuthState.SESSION_EXPIRED, "Mock session expired.", "SESSION_EXPIRED", self._save_auth_screenshot(AuthState.SESSION_EXPIRED))
            self.page.locator("text=Dashboard").first.wait_for(state="visible", timeout=int(self.settings.get("authentication_timeout_ms", 180000)))
            transition(AuthState.AUTHENTICATION_VERIFY, "CAPTCHA accepted; verifying direct dashboard login.")
            transition(AuthState.AUTHENTICATED, "Mock dashboard confirmed.")
            self.capture_evidence("After Login", "success")
            transition(AuthState.REGISTRATION_READY, "CAPTCHA-only authentication checkpoint complete; registration may continue.")
            return AuthenticationResult(AuthState.REGISTRATION_READY, "Mock CAPTCHA authentication succeeded.")
        except SeleniumTimeoutError as exc:
            text = self._page_text()
            if "session expired" in text:
                error_type, state = "SESSION_EXPIRED", AuthState.SESSION_EXPIRED
            else:
                error_type, state = "CAPTCHA_TIMEOUT", AuthState.TIMEOUT
            return AuthenticationResult(state, f"Mock authentication failed: {exc}", error_type, self._save_auth_screenshot(state))
        except Exception as exc:
            text = self._page_text()
            if "invalid captcha" in text:
                error_type = "CAPTCHA_INVALID"
            else:
                error_type = "AUTHENTICATION_FAILURE"
            return AuthenticationResult(AuthState.AUTHENTICATION_FAILED, str(exc), error_type, self._save_auth_screenshot(AuthState.AUTHENTICATION_FAILED))

    def _classify_auth_failure(self, text: str) -> tuple[str, str]:
        return "Authentication Failure", "EPFO authentication failed. No automated retry was attempted."

    def _classify_failure(self, text: str) -> tuple[str, str]:
        if any(x in text for x in ("already registered", "already exists", "duplicate", "already active")):
            return "Duplicate Member", "Duplicate or already-active member detected by portal."
        if any(x in text for x in ("not matched", "mismatch", "invalid aadhaar", "invalid uan", "does not match")):
            return "Member Validation Mismatch", "Member validation mismatch reported by portal."
        if any(x in text for x in ("timed out", "timeout", "temporarily unavailable", "service unavailable", "502", "503")):
            return "Network or Portal Availability", "Portal/network timeout or temporary unavailability."
        return "Automation Error", "The expected control or portal response was not found."

    def _step(self, name: str):
        self.current_step = name
        self.log(f"Workflow step: {name}")
        self._demo_wait()

    def _confirm_alert_if_present(self):
        """Dismiss EPFO's informational home-page modal, if it is rendered."""
        if auth_mode(self.settings) != "MOCK":
            try:
                # EPFO shows this modal shortly after DOMContentLoaded. Waiting
                # for it avoids a late overlay intercepting the username click.
                self.page.locator("#mainHomePageAlertModal").wait_for(state="visible", timeout=5000)
            except Exception:
                pass
        for selector in ("#btnCloseModal", "button[id*='btnCloseModal' i]", "#mainHomePageAlertModal button"):
            try:
                control = self.page.locator(selector)
                if control.is_visible(timeout=350):
                    control.click(timeout=1500)
                    try:
                        self.page.locator("#mainHomePageAlertModal").wait_for(state="hidden", timeout=3000)
                    except Exception:
                        pass
                    self.log("EPFO security alert acknowledged; login panel is available.")
                    self.page.wait_for_timeout(250)
                    return True
            except Exception:
                continue
        try:
            self.page.once("dialog", lambda dialog: dialog.accept())
        except Exception:
            pass
        return False

    def _dismiss_post_login_alert(self):
        """Dismiss EPFO's post-login alert before continuing the workflow."""
        selectors = (
            "#mainHomePageAlertModal input[value='OK']",
            "#mainHomePageAlertModal button:has-text('OK')",
            "#mainHomePageAlertModal .btn-danger",
            "#mainHomePageAlertModal .btn-success",
        )
        for selector in selectors:
            try:
                control = self.page.locator(selector)
                if control.is_visible(timeout=1000):
                    control.click(timeout=2500)
                    self.page.wait_for_timeout(400)
                    self.log("Post-login EPFO alert acknowledged with OK.")
                    return True
            except Exception:
                continue
        return False

    def login_page(self, username: str, password: str, target_url: Optional[str] = None):
        self._step("Open login page")
        self.page.goto(target_url or self.settings["portal_url"])
        # Start every employer session from a fresh portal state before credentials are entered.
        self.page.reload(wait_until="domcontentloaded")
        self.log("EPFO login page refreshed before credentials were entered.")
        self.log("EPFO login page opened.")
        if getattr(self.page, "access_blocked", lambda: False)():
            screenshot = self._save_error_screenshot("access_denied", "Open login page")
            raise PortalLayoutChange("PORTAL_ACCESS_BLOCKED: EPFO/WAF denied this browser session before login.", screenshot)
        self._confirm_alert_if_present()
        self.page.set_page_zoom(float(self.settings.get("captcha_capture_zoom", 1.0)))
        self.log("EPFO alert handled; login controls are ready.")
        self._assert_contract("login")
        try:
            self._type_verified(self._selectors("login_username", ["#username1", "input[name='username']", "input[placeholder*='User']"]), username)
            self._type_verified(self._selectors("login_password", ["#password", "input[name='password']"]), password)
        except PortalLayoutChange as exc:
            screenshot = exc.screenshot or self._save_layout_screenshot("login")
            raise PortalLayoutChange(str(exc), screenshot) from exc
        self.capture_evidence("Before Login")
        self.log("Login ID and password filled; CAPTCHA and Sign In will be handled automatically.")

    def wait_for_manual_login(self):
        self.page.wait_for_url(re.compile(r"unifiedportal-emp\.epfindia\.gov\.in/epfo/.*(?!/epfo/$)"), timeout=0)

    def open_register_individual(self):
        last_error = None
        for attempt in range(1, 4):
            try:
                if self.mock_base_url:
                    self.page.goto(f"{self.mock_base_url}/register")
                else:
                    # The live EPFO portal opens registration from the Member
                    # menu and can show an informational modal on first load.
                    alerts = self.page.locator("#employeeEnrollmetModal")
                    try:
                        alerts.first.wait_for(state="visible", timeout=8000)
                        self.page.locator("#employeeEnrollmetModal button:has-text('OK')").first.click()
                        alerts.first.wait_for(state="hidden", timeout=8000)
                    except SeleniumTimeoutError:
                        pass
                    self._click(self._selectors("member_menu"))
                    self._click(self._selectors("register_individual_menu"))
                    self.page.locator("#previousEmployementYes").wait_for(state="visible", timeout=35000)
                self.log("Member Registration page opened.")
                if self.mock_base_url:
                    self._assert_contract("registration_identity")
                elif not self._visible(["#previousEmployementYes"]):
                    raise PortalLayoutChange("PORTAL_LAYOUT_CHANGE: previous-UAN control is absent from Member Registration.")
                break
            except Exception as exc:
                if isinstance(exc, PortalLayoutChange):
                    raise
                last_error = exc
                if attempt < 3:
                    delay = min(8, 2 ** attempt)
                    self.log(f"Portal navigation failed; retrying after bounded backoff ({delay}s; {attempt}/3).")
                    time.sleep(delay)
                else:
                    raise last_error
        # The portal may show a UAN-generation notice on first load.
        for text in ["OK", "Close"]:
            try:
                self.page.get_by_role("button", name=text, exact=True).click(timeout=1500)
                break
            except Exception:
                pass

    def _register_one_live(self, record: dict[str, str]) -> RegistrationResult:
        """Process one verified-UAN member in the live EPFO form.

        This is deliberately separated from the local mock flow because the
        live portal exposes a different set of controls and a confirmation
        modal after identity verification.  It never approves a member; the
        terminal state is the portal's Registered - Pending Approval result.
        """
        row, name = record["row_number"], record["name"]
        page = self.page.page
        def portal_date(value: str) -> str:
            for date_format in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(value, date_format).strftime("%d/%m/%Y")
                except ValueError:
                    continue
            raise ValueError("The validated date cannot be entered in EPFO's DD/MM/YYYY form.")

        portal_dob = portal_date(record["dob"])
        portal_doj = portal_date(record["doj"])
        self._step("Select previous UAN")
        page.locator("#previousEmployementYes").check()
        page.locator("#uan").wait_for(state="visible", timeout=20000)
        for label, selector, value in (
            ("UAN", "#uan", re.sub(r"\D", "", record["uan"])),
            ("Aadhaar", "#aadharVerify", re.sub(r"\D", "", record["aadhaar"])),
            ("Name", "#nameVerify", name),
            ("Date of birth", "#dobVerify", portal_dob),
        ):
            self._step(f"Enter {label}")
            field = page.locator(selector)
            field.fill(value)
            if field.input_value() != value:
                raise ValueError(f"EPFO changed the {label} value before verification.")
        page.locator("#aadhaarConsentChkBox").check()
        self.capture_evidence("Data Entry Steps")
        self._step("Verify existing member details")
        page.locator("input.btn-verify[value='Verify']").click()
        matched = page.get_by_text("Member details matched", exact=False)
        try:
            matched.wait_for(state="visible", timeout=30000)
        except SeleniumTimeoutError as exc:
            visible_text = self._page_text()
            raise RuntimeError(f"MEMBER_VALIDATION_MISMATCH: EPFO did not confirm the member details. {visible_text[-700:]}") from exc
        modal = page.locator(".modal:visible").filter(has=matched).first
        modal_text = modal.inner_text(timeout=3000)
        if norm(name) not in norm(modal_text) or portal_dob not in modal_text:
            raise RuntimeError("MEMBER_VALIDATION_MISMATCH: EPFO returned different name or birth date.")
        if record.get("gender") and norm(record["gender"]) not in norm(modal_text):
            raise RuntimeError("MEMBER_VALIDATION_MISMATCH: EPFO returned a different gender.")
        if re.sub(r"\D", "", record["aadhaar"])[-4:] not in modal_text:
            raise RuntimeError("MEMBER_VALIDATION_MISMATCH: EPFO returned a different Aadhaar suffix.")
        try:
            modal.get_by_role("button", name="Ok", exact=True).click()
        except Exception:
            # EPFO has used both title-case variants across portal builds.
            modal.get_by_role("button", name="OK", exact=True).click()
        page.locator("#savePreviousMemberButton").wait_for(state="visible", timeout=20000)
        self._step("Confirm portal member data")
        marital = page.locator("#maritalStatus")
        marital_value = marital.input_value()
        if not marital_value or marital_value == "X":
            raise RuntimeError("EPFO did not supply a valid marital status for the verified UAN.")
        supplied_marital = record.get("marital_status", "").strip()
        if supplied_marital:
            selected = marital.locator("option:checked").inner_text()
            if norm(supplied_marital) != norm(selected):
                raise RuntimeError("MEMBER_VALIDATION_MISMATCH: supplied marital status differs from EPFO's verified record.")
        page_text = self._page_text()
        if record.get("father_name") and norm(record["father_name"]) not in norm(page_text):
            raise RuntimeError("MEMBER_VALIDATION_MISMATCH: father name differs from EPFO's verified record.")
        if record.get("mobile") and record["mobile"] not in page_text:
            raise RuntimeError("MEMBER_VALIDATION_MISMATCH: mobile differs from EPFO's verified record.")
        self.log(f"Row {row}: EPFO supplied marital status and existing member details for {name}.")
        self._step("Enter date of joining")
        page.locator("#doj").fill(portal_doj)
        self._step("Enter monthly EPF wage")
        page.locator("#wages").fill(record["basic_wage"])
        if page.locator("#doj").input_value() != portal_doj or page.locator("#wages").input_value() != record["basic_wage"]:
            raise RuntimeError("EPFO changed the joining date or wage before Save.")
        self.capture_evidence("Data Entry Steps")
        self._step("Save member registration")
        before_save_text = self._page_text()
        dialogs: list[str] = []

        def handle_save_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.accept()

        page.on("dialog", handle_save_dialog)
        try:
            page.locator("#wages").press("Tab")
            page.locator("#savePreviousMemberButton").click()
            self._step("Confirm portal save result")
            deadline = time.monotonic() + 45
            result_text = ""
            while time.monotonic() < deadline:
                alerts = page.locator(".alert-danger:visible, .alert-success:visible, .alert-warning:visible")
                result_text = " ".join(alerts.all_inner_texts()) + " " + " ".join(dialogs)
                page_text = self._page_text()
                if any(message in page_text and message not in before_save_text for message in (
                    "member is already active in current establishment",
                    "member already registered in current establishment",
                )):
                    screenshot = self._save_error_screenshot(row, self.current_step)
                    return RegistrationResult(row, name, "Blocked - Existing Active Member", "EPFO reports this member is already active in the current establishment. Review the existing member record before any retry.", error_type="DUPLICATE_ACTIVE_MEMBER", workflow_step=self.current_step, screenshot=screenshot)
                if "pending for approval" in result_text.lower() and re.sub(r"\D", "", record["uan"]) in result_text and norm(name) in norm(result_text):
                    screenshot = self._save_error_screenshot(row, self.current_step)
                    return RegistrationResult(row, name, "Blocked - Existing Pending Registration", "EPFO reports this member and UAN already in pending approval transactions. This account's member search did not show the record; its owner must be checked before any further submission.", error_type="DUPLICATE_PENDING_MEMBER", workflow_step=self.current_step, screenshot=screenshot)
                success_pattern = r"(?:member|registration).{0,80}(?:saved|registered) successfully|successfully (?:saved|registered)"
                if re.search(success_pattern, result_text, re.I) or (re.search(success_pattern, page_text, re.I) and not re.search(success_pattern, before_save_text, re.I)):
                    evidence = self.capture_evidence("Submission", "success")
                    return RegistrationResult(row, name, "Registered - Pending Approval", "EPFO explicitly confirmed this member was saved; approval remains pending.", workflow_step=self.current_step, screenshot=evidence)
                page.wait_for_timeout(250)
            screenshot = self._save_error_screenshot(row, self.current_step)
            return RegistrationResult(row, name, "Save Result Unknown", "Save was clicked once, but EPFO gave no explicit result within 45 seconds. Inspect pending transactions before retrying.", error_type="SAVE_RESULT_UNKNOWN", workflow_step=self.current_step, screenshot=screenshot)
        finally:
            page.remove_listener("dialog", handle_save_dialog)

    def register_one(self, record: dict[str, str]) -> RegistrationResult:
        row = record["row_number"]
        name = record.get("name", "")
        try:
            self._step("Open individual registration page")
            self.open_register_individual()
            if not self.mock_base_url:
                return self._register_one_live(record)
            self._step("Select previous UAN")
            self._click(self._selectors("previous_uan_yes", ["input[type='radio'][value='Y']", "label:has-text('Yes')", "text=Yes"]))
            self._step("Enter UAN")
            self._fill(self._selectors("uan", ["input[name*='uan' i]", "input[id*='uan' i]"]), re.sub(r"\D", "", record["uan"]))
            self.capture_evidence("Data Entry Steps")
            self._step("Enter Aadhaar")
            self._fill(self._selectors("aadhaar", ["input[name*='aadhaar' i]", "input[name*='aadhar' i]", "input[id*='aadhaar' i]"]), re.sub(r"\D", "", record["aadhaar"]))
            self.capture_evidence("Data Entry Steps")
            # The current portal asks for consent before Verify.
            for selector in ["input[type='checkbox']", "label:has-text('consent')"]:
                try:
                    loc = self.page.locator(selector).first
                    if loc.is_visible() and not loc.is_checked():
                        loc.check()
                        break
                except Exception:
                    pass
            # EPFO requires this technical identity check before it exposes the
            # registration form. It is an internal portal step, not a separate
            # user-facing intake requirement.
            self._step("Prepare PF registration")
            self._click(self._selectors("verify", ["button:has-text('Verify')", "input[value='Verify']", "text=Verify"]))
            self._click(["button:has-text('OK')", "text=OK"])
            identity_text = self._page_text()
            if any(marker in identity_text for marker in ("mismatch", "not matched", "invalid aadhaar", "invalid uan")):
                raise RuntimeError("MEMBER_VALIDATION_MISMATCH: portal identity details did not match.")
            if not self._visible(self._selectors("identity_success", [])) and "verified" not in identity_text:
                raise RuntimeError("MEMBER_VALIDATION_MISMATCH: member identity verification was not confirmed.")
            self.log(f"Row {row}: portal identity check completed internally for {name}.")

            self._assert_contract("registration_details")
            self._step("Enter date of joining")
            self._fill(self._selectors("joining_date", ["input[name*='joining' i]", "input[id*='joining' i]"]), record["doj"])
            self.capture_evidence("Data Entry Steps")
            self._step("Enter monthly EPF wage")
            self._fill(self._selectors("wage", ["input[name*='wage' i]", "input[id*='wage' i]"]), record["basic_wage"])
            self.capture_evidence("Data Entry Steps")
            # Optional values, when present in the workbook.
            optional = {
                "mobile": ["input[name*='mobile' i]", "input[id*='mobile' i]"],
                "email": ["input[name*='email' i]", "input[id*='email' i]"],
                "father_name": ["input[name*='father' i]", "input[id*='father' i]"],
            }
            for key, selectors in optional.items():
                if record.get(key):
                    self._step(f"Fill {key}")
                    try:
                        self._fill(selectors, record[key])
                    except Exception as exc:
                        raise RuntimeError(f"Field unavailable or not editable: {key} ({exc})") from exc
            self.capture_evidence("Data Entry Steps")
            self._step("Save member registration")
            self.capture_evidence("Submission")
            self.log(f"Row {row}: employment details entered; approval is intentionally not performed.")
            self._click(self._selectors("save", ["button:has-text('Save')", "input[value='Save']", "text=Save"]))
            self._step("Confirm Registered - Pending Approval")
            if not self._wait_for_any(self._selectors("pending_approval", ["text=Registered - Pending Approval", "text=Pending Approval"]), self.timeout_ms):
                screenshot = self._save_error_screenshot(row, self.current_step)
                return RegistrationResult(row, name, "Save Result Unknown", "Save was submitted but Pending Approval could not be confirmed; no automatic resubmission was attempted. Check for a portal layout change.", error_type="PORTAL_LAYOUT_CHANGE_OR_SAVE_RESULT_UNKNOWN", workflow_step=self.current_step, screenshot=screenshot)
            try:
                self._click(["button:has-text('OK')", "text=OK"])
            except Exception:
                pass
            self.capture_evidence("Submission", "success")
            return RegistrationResult(row, name, "Registered - Pending Approval", "Saved successfully; stopped before approval.", workflow_step=self.current_step)
        except PortalLayoutChange as exc:
            self.capture_evidence("Submission", "failure")
            screenshot = exc.screenshot or self._save_error_screenshot(row, self.current_step)
            return RegistrationResult(row, name, "Failed", str(exc), error_type="PORTAL_LAYOUT_CHANGE", workflow_step=self.current_step, screenshot=screenshot)
        except SeleniumTimeoutError as exc:
            self.capture_evidence("Submission", "failure")
            error_type, message = self._classify_failure(self._page_text())
            message = f"{message} ({exc})"
            screenshot = self._save_error_screenshot(row, self.current_step)
            return RegistrationResult(row, name, "Failed", message, error_type=error_type, workflow_step=self.current_step, screenshot=screenshot)
        except Exception as exc:
            self.capture_evidence("Submission", "failure")
            error_type, default = self._classify_failure(self._page_text())
            message = str(exc) or default
            if "unavailable or not editable" in message:
                error_type = "Field Validation or Portal Layout Change"
            if "MEMBER_VALIDATION_MISMATCH" in message:
                error_type = "REG-001 MEMBER_VALIDATION_MISMATCH"
            screenshot = self._save_error_screenshot(row, self.current_step)
            return RegistrationResult(row, name, "Failed", message, error_type=error_type, workflow_step=self.current_step, screenshot=screenshot)

    def _save_error_screenshot(self, row: str, step: str) -> str:
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            safe_step = re.sub(r"[^A-Za-z0-9_-]+", "_", step).strip("_") or "unknown_step"
            target = self.artifact_dir / f"row_{row}_{safe_step}_error.png"
            self.page.screenshot(path=str(target), full_page=True)
            return str(target)
        except Exception:
            return ""
