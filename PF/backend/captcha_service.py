"""Detect the rendered EPFO CAPTCHA and track challenge changes."""

from __future__ import annotations

import hashlib
import base64
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Optional


class CaptchaType(str, Enum):
    NONE = "NONE"
    IMAGE = "IMAGE"
    UNSUPPORTED = "UNSUPPORTED"


class CaptchaStatus(str, Enum):
    NOT_PRESENT = "NOT_PRESENT"
    PRESENT = "PRESENT"
    CHANGED = "CHANGED"
    EXPIRED = "EXPIRED"
    UNAVAILABLE = "UNAVAILABLE"


class DetectionStatus(str, Enum):
    CAPTCHA_DETECTED = "CAPTCHA_DETECTED"
    GATEWAY_BLOCKED = "GATEWAY_BLOCKED"
    CAPTCHA_NOT_FOUND = "CAPTCHA_NOT_FOUND"
    PAGE_UNAVAILABLE = "PAGE_UNAVAILABLE"


@dataclass(frozen=True)
class CaptchaObservation:
    status: CaptchaStatus
    captcha_type: CaptchaType
    fingerprint: str = ""
    evidence_path: str = ""
    message: str = ""
    observed_at: str = ""

    def public_dict(self) -> dict[str, str]:
        """Return safe telemetry; never include challenge text or image bytes."""
        return {
            "status": self.status.value,
            "captcha_type": self.captcha_type.value,
            "fingerprint": self.fingerprint,
            "evidence_path": self.evidence_path,
            "message": self.message,
            "observed_at": self.observed_at,
        }


class _FrameElement:
    """Element wrapper that restores the top document after evidence capture."""
    def __init__(self, driver, element): self.driver, self.element = driver, element
    def screenshot(self, path=None, type="png"):
        data = self.element.screenshot_as_png
        if path: Path(path).write_bytes(data)
        return data


class CaptchaDetector:
    """Detect the configured image control through the browser page adapter."""

    def __init__(
        self,
        page,
        selectors: Callable[[], Iterable[str]],
        artifact_dir: Path,
        screenshot_enabled: bool = True,
    ) -> None:
        self.page = page
        self.selectors = selectors
        self.artifact_dir = Path(artifact_dir)
        self.screenshot_enabled = screenshot_enabled
        self._last_fingerprint = ""
        self.last_observation = CaptchaObservation(CaptchaStatus.NOT_PRESENT, CaptchaType.NONE)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _visible_locator(self):
        for selector in self.selectors():
            try:
                locator = self.page.locator(selector).first
                if locator.is_visible(timeout=500):
                    return locator
            except Exception:
                continue
        # CAPTCHA widgets are sometimes rendered inside same-origin iframes.
        # Inspect frame documents without reading challenge text.
        driver = getattr(self.page, "driver", None)
        if driver is not None:
            try:
                frames = driver.find_elements("css selector", "iframe")
                for frame in frames:
                    try:
                        driver.switch_to.frame(frame)
                        for selector in self.selectors():
                            matches = driver.find_elements("css selector", selector)
                            for element in matches:
                                if element.is_displayed():
                                    return _FrameElement(driver, element)
                    finally:
                        driver.switch_to.default_content()
            except Exception:
                try: driver.switch_to.default_content()
                except Exception: pass
        return None

    def image_bytes(self) -> tuple[bytes, str]:
        """Return native image pixels and fingerprint without writing them."""
        locator = self._visible_locator()
        if locator is None:
            raise RuntimeError("CAPTCHA image is not visible.")
        try:
            encoded = locator.evaluate("""image => {
                if (!image.complete || !image.naturalWidth) throw Error('CAPTCHA image is not loaded');
                const canvas = document.createElement('canvas');
                canvas.width = image.naturalWidth;
                canvas.height = image.naturalHeight;
                canvas.getContext('2d').drawImage(image, 0, 0);
                return canvas.toDataURL('image/png').split(',')[1];
            }""")
            data = base64.b64decode(encoded)
        except Exception:
            data = locator.screenshot(type="png")
        if not data:
            raise RuntimeError("CAPTCHA image could not be captured.")
        return data, hashlib.sha256(data).hexdigest()[:16]

    @staticmethod
    def _fingerprint(locator) -> str:
        """Fingerprint rendered pixels/markup without extracting challenge text."""
        try:
            data = locator.screenshot(type="png")
            if data:
                return hashlib.sha256(data).hexdigest()[:16]
        except Exception:
            pass
        return ""

    def _evidence(self, locator, fingerprint: str) -> str:
        if not self.screenshot_enabled:
            return ""
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            suffix = fingerprint or "unavailable"
            target = self.artifact_dir / f"captcha_checkpoint_{suffix}.png"
            # Capture only the challenge element. This prevents credentials or
            # other page content from entering the evidence artifact.
            locator.screenshot(path=str(target), type="png")
            return str(target)
        except Exception:
            return ""

    def observe(self, capture: bool = True) -> CaptchaObservation:
        observed_at = self._now()
        try:
            locator = self._visible_locator()
        except Exception as exc:
            result = CaptchaObservation(
                CaptchaStatus.UNAVAILABLE,
                CaptchaType.UNSUPPORTED,
                message=f"CAPTCHA control could not be inspected: {type(exc).__name__}",
                observed_at=observed_at,
            )
            self.last_observation = result
            return result
        if locator is None:
            result = CaptchaObservation(CaptchaStatus.NOT_PRESENT, CaptchaType.NONE, observed_at=observed_at)
            self.last_observation = result
            return result

        fingerprint = self._fingerprint(locator)
        changed = bool(self._last_fingerprint and fingerprint and fingerprint != self._last_fingerprint)
        status = CaptchaStatus.CHANGED if changed else CaptchaStatus.PRESENT
        evidence = self._evidence(locator, fingerprint) if capture else ""
        self._last_fingerprint = fingerprint or self._last_fingerprint
        result = CaptchaObservation(
            status,
            CaptchaType.IMAGE,
            fingerprint=fingerprint,
            evidence_path=evidence,
            message="CAPTCHA image detected.",
            observed_at=observed_at,
        )
        self.last_observation = result
        return result

    def mark_expired(self, message: str = "CAPTCHA checkpoint expired before completion.") -> CaptchaObservation:
        result = CaptchaObservation(CaptchaStatus.EXPIRED, CaptchaType.IMAGE, message=message, observed_at=self._now())
        self.last_observation = result
        return result

    def classify(self) -> DetectionStatus:
        """Return a public, policy-safe page classification.

        This method never reads CAPTCHA characters and never performs any
        security-checkpoint action.
        """
        page = self.page
        try:
            if getattr(page, "access_blocked", lambda: False)():
                return DetectionStatus.GATEWAY_BLOCKED
            observation = self.observe(capture=False)
            if observation.status in (CaptchaStatus.PRESENT, CaptchaStatus.CHANGED):
                return DetectionStatus.CAPTCHA_DETECTED
            if observation.status in (CaptchaStatus.UNAVAILABLE, CaptchaStatus.EXPIRED):
                return DetectionStatus.PAGE_UNAVAILABLE
            return DetectionStatus.CAPTCHA_NOT_FOUND
        except Exception:
            return DetectionStatus.PAGE_UNAVAILABLE

    def classify_result(self, capture: bool = False) -> dict[str, str]:
        status = self.classify()
        observation = self.observe(capture=capture) if status == DetectionStatus.CAPTCHA_DETECTED else self.last_observation
        result = observation.public_dict()
        result["classification"] = status.value
        return result
