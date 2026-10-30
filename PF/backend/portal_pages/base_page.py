from __future__ import annotations

import re
import time
from pathlib import Path

from .errors import PortalLayoutChange


class PortalPage:
    """Shared Selenium operations and fail-closed selector contracts."""

    def __init__(self, page, config, artifact_dir: Path, log):
        self.page = page
        self.config = config
        self.artifact_dir = Path(artifact_dir)
        self.log = log

    def selectors(self, key: str, fallback=None):
        return self.config.selectors_for(key, fallback or [])

    def contract(self, name: str):
        return self.config.contract(name)

    def visible(self, selectors):
        return any(self.page.locator(selector).first.is_visible(timeout=500) for selector in selectors)

    def first(self, selectors):
        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                locator.wait_for(state="visible", timeout=2500)
                return locator
            except Exception:
                continue
        raise PortalLayoutChange("PORTAL_LAYOUT_CHANGE: no configured selector matched the current page.")

    def fill(self, selectors, value):
        self.first(selectors).fill(value)

    def click(self, selectors):
        self.first(selectors).click()

    def assert_contract(self, name: str):
        keys = self.contract(name)
        missing = [key for key in keys if not self.visible(self.selectors(key))]
        if missing:
            screenshot = self.screenshot(f"contract_{name}")
            raise PortalLayoutChange(
                f"PORTAL_LAYOUT_CHANGE: contract '{name}' is missing visible controls: {', '.join(missing)}.",
                screenshot,
            )

    def screenshot(self, name: str):
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name)
            target = self.artifact_dir / f"{safe}.png"
            self.page.screenshot(path=str(target), full_page=True)
            return str(target)
        except Exception:
            return ""

    def demo_wait(self, settings):
        delay = int(settings.get("mock_demo_delay_ms", 0) or 0)
        if delay:
            time.sleep(delay / 1000)
