from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class AuthState(str, Enum):
    LOGIN_PAGE = "LOGIN_PAGE"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    CAPTCHA_PROCESSING = "CAPTCHA_PROCESSING"
    CAPTCHA_VALID = "CAPTCHA_VALID"
    LOGIN_TIMEOUT = "LOGIN_TIMEOUT"
    WAITING_FOR_OPERATOR = "WAITING_FOR_OPERATOR"
    AUTHENTICATION_VERIFY = "AUTHENTICATION_VERIFY"
    AUTHENTICATED = "AUTHENTICATED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    REGISTRATION_READY = "REGISTRATION_READY"
    UNEXPECTED_PORTAL_STATE = "UNEXPECTED_PORTAL_STATE"
    TIMEOUT = "TIMEOUT"


@dataclass
class AuthenticationResult:
    state: AuthState
    message: str
    error_type: str = ""
    screenshot: str = ""


LogFn = Callable[[str], None]
StateFn = Callable[[AuthState, str], None]


class MockAuthenticationProvider:
    """Local-only authentication simulator. It never opens or contacts EPFO."""

    SCENARIOS = {
        "SUCCESS",
        "TIMEOUT",
        "SESSION_EXPIRED",
        "AUTHENTICATION_FAILURE",
    }

    def __init__(self, scenario: str = "SUCCESS", log: Optional[LogFn] = None):
        self.scenario = scenario.upper()
        self.log = log or (lambda _message: None)
        if self.scenario not in self.SCENARIOS:
            raise ValueError(f"Unsupported mock authentication scenario: {self.scenario}")

    def authenticate(self, on_state: Optional[StateFn] = None) -> AuthenticationResult:
        def transition(state: AuthState, message: str):
            self.log(f"Authentication state: {state.value} — {message}")
            if on_state:
                on_state(state, message)

        transition(AuthState.LOGIN_PAGE, "Mock login page detected.")
        transition(AuthState.CAPTCHA_REQUIRED, "Mock CAPTCHA checkpoint detected.")
        transition(AuthState.WAITING_FOR_OPERATOR, "Waiting for the mock CAPTCHA checkpoint.")

        if self.scenario == "SUCCESS":
            transition(AuthState.AUTHENTICATION_VERIFY, "Verifying mock authenticated dashboard.")
            transition(AuthState.AUTHENTICATED, "Mock authenticated dashboard confirmed.")
            transition(AuthState.REGISTRATION_READY, "Mock registration route is ready.")
            return AuthenticationResult(AuthState.REGISTRATION_READY, "Mock authentication succeeded.")
        if self.scenario == "SESSION_EXPIRED":
            transition(AuthState.SESSION_EXPIRED, "Mock session expired.")
            return AuthenticationResult(AuthState.SESSION_EXPIRED, "Mock session expired.", "Session Expired")
        if self.scenario == "AUTHENTICATION_FAILURE":
            transition(AuthState.AUTHENTICATION_FAILED, "Mock authentication failure.")
            return AuthenticationResult(AuthState.AUTHENTICATION_FAILED, "Mock authentication failure.", "Authentication Failure")
        transition(AuthState.TIMEOUT, "Mock authentication timeout.")
        return AuthenticationResult(AuthState.TIMEOUT, "Mock authentication timeout.", "Authentication Timeout")


def ensure_auth_environment(settings: dict, mode: str) -> None:
    """Fail closed if authentication mode and endpoint are ambiguous."""
    portal_url = str(settings.get("portal_url", ""))
    if mode == "MOCK":
        base_url = str(settings.get("mock_base_url", ""))
        if base_url and not base_url.lower().startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
            raise ValueError("MOCK authentication must use a localhost mock endpoint.")
        return
    if mode == "REAL":
        if not portal_url.lower().startswith("https://unifiedportal-emp.epfindia.gov.in/"):
            raise ValueError("REAL authentication endpoint is not the explicitly approved EPFO URL.")
        return
    raise ValueError(f"Unsupported authentication mode: {mode}")


def auth_mode(settings: dict) -> str:
    return os.getenv("EPFO_AUTH_MODE", settings.get("auth_mode", "REAL")).upper()


def mock_scenario(settings: dict) -> str:
    return os.getenv("EPFO_MOCK_SCENARIO", settings.get("mock_scenario", "SUCCESS")).upper()
