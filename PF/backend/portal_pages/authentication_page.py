from __future__ import annotations

from Platform.backend.authentication import AuthState
from PF.backend.captcha_service import CaptchaStatus, CaptchaDetector, DetectionStatus
from .base_page import PortalPage


class AuthenticationPage(PortalPage):
    """Login-page controls, dashboard contract, and CAPTCHA checkpoint."""

    def __init__(self, page, config, artifact_dir, log, settings):
        super().__init__(page, config, artifact_dir, log)
        self.settings = settings
        self.captcha = CaptchaDetector(page, lambda: self.selectors("captcha"), artifact_dir, bool(settings.get("screenshot_on_failure", True)))

    def open_and_fill_credentials(self, url, username, password):
        self.page.goto(url)
        self.assert_contract("login")
        self.fill(self.selectors("login_username"), username)
        self.fill(self.selectors("login_password"), password)

    def detect_state(self):
        body = self.page.locator("body").inner_text(timeout=3000).lower()
        if self.visible(self.selectors("session_expired", [])) or "session expired" in body:
            return AuthState.SESSION_EXPIRED
        if self.visible(self.selectors("auth_failure", [])) or "invalid credentials" in body:
            return AuthState.AUTHENTICATION_FAILED
        if self.visible(self.selectors("auth_success", [])):
            return AuthState.AUTHENTICATED
        observation = self.captcha.observe(capture=False)
        if observation.status in (CaptchaStatus.PRESENT, CaptchaStatus.CHANGED) or "enter captcha" in body:
            return AuthState.CAPTCHA_REQUIRED
        if self.visible(self.selectors("login_password", [])):
            return AuthState.LOGIN_PAGE
        return AuthState.UNEXPECTED_PORTAL_STATE

    def capture_captcha_checkpoint(self):
        return self.captcha.observe(capture=True)

    def detection_result(self, capture: bool = False):
        return self.captcha.classify_result(capture=capture)
