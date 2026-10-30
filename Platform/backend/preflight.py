from __future__ import annotations

import json
import os
import shutil
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from Platform.backend.authentication import auth_mode, ensure_auth_environment


@dataclass
class PreflightResult:
    passed: bool
    checks: list[dict[str, str]] = field(default_factory=list)

    @property
    def failures(self) -> list[dict[str, str]]:
        return [check for check in self.checks if check["status"] != "PASS"]


def _check(checks, name: str, passed: bool, message: str):
    checks.append({"name": name, "status": "PASS" if passed else "FAIL", "message": message})


def run_preflight(workbook: Path, artifact_dir: Path, settings: dict, *, check_portal: bool = True) -> PreflightResult:
    checks: list[dict[str, str]] = []
    mode = auth_mode(settings)
    try:
        ensure_auth_environment(settings, mode)
        _check(checks, "Authentication mode", True, mode)
    except Exception as exc:
        _check(checks, "Authentication mode", False, str(exc))
    _check(checks, "Workbook exists", workbook.exists(), str(workbook))
    _check(checks, "Workbook extension", workbook.suffix.lower() in {".xlsx", ".xlsm"}, workbook.suffix)
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        probe = artifact_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        _check(checks, "Artifact directory writable", True, str(artifact_dir))
    except Exception as exc:
        _check(checks, "Artifact directory writable", False, str(exc))
    try:
        from Platform.backend.automation.playwright_page import create_playwright_browser
        browser = create_playwright_browser(headless=True)
        browser.close()
        _check(checks, "Playwright browser available", True, "Playwright Chromium/Edge adapter")
    except Exception as exc:
        _check(checks, "Chromium executable available", False, str(exc))
    selectors_path = Path(__file__).resolve().parent / "config" / "selectors.json"
    try:
        selectors = json.loads(selectors_path.read_text(encoding="utf-8"))
        critical = (
            "login_username",
            "login_password",
            "login_submit",
            "captcha",
            "captcha_input",
            "member_menu",
            "register_individual_menu",
            "uan",
            "aadhaar",
            "verify",
            "joining_date",
            "wage",
            "save",
        )
        missing = [key for key in critical if not selectors.get(key)]
        _check(checks, "Critical selector configuration", not missing, "Missing: " + ", ".join(missing))
    except Exception as exc:
        _check(checks, "Critical selector configuration", False, str(exc))
    if mode == "REAL":
        try:
            from rapidocr import RapidOCR  # noqa: F401
            import cv2  # noqa: F401
            from PIL import Image  # noqa: F401
            model = Path(__file__).resolve().parents[2] / "PF" / "backend" / "models" / "ocr-glyphs-python.json"
            _check(checks, "CAPTCHA OCR available", model.is_file(), "Trained glyph model, Pillow, RapidOCR, and OpenCV installed" if model.is_file() else "Trained glyph model is missing")
        except ImportError as exc:
            _check(checks, "CAPTCHA OCR available", False, f"Install backend requirements: {type(exc).__name__}")
    if mode == "REAL" and check_portal:
        try:
            request = urllib.request.Request(settings["portal_url"], method="GET", headers={"User-Agent": "EPFO-Automation-Preflight/1.0"})
            with urllib.request.urlopen(request, timeout=10) as response:
                _check(checks, "Portal reachable", response.status < 500, f"HTTP {response.status}")
        except Exception as exc:
            _check(checks, "Portal reachable", False, str(exc))
    elif mode == "MOCK":
        _check(checks, "Mock endpoint isolation", True, "The application creates a 127.0.0.1 server for MOCK mode")
    return PreflightResult(not any(check["status"] != "PASS" for check in checks), checks)
