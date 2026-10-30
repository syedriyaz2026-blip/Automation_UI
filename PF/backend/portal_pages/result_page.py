from __future__ import annotations

from .base_page import PortalPage


class ResultPage(PortalPage):
    """Save action and confirmation; approval actions are intentionally absent."""

    def save_and_confirm(self, timeout_ms: int):
        self.click(self.selectors("save"))
        deadline = __import__("time").monotonic() + timeout_ms / 1000
        while __import__("time").monotonic() < deadline:
            if self.visible(self.selectors("pending_approval")):
                return True
            __import__("time").sleep(0.25)
        return False
