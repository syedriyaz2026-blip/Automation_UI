from __future__ import annotations

import re
from .base_page import PortalPage


class IdentityPage(PortalPage):
    """Previous-UAN selection and UAN/Aadhaar identity verification."""

    def verify(self, record):
        self.assert_contract("registration_identity")
        self.click(self.selectors("previous_uan_yes"))
        self.fill(self.selectors("uan"), re.sub(r"\D", "", record["uan"]))
        self.fill(self.selectors("aadhaar"), re.sub(r"\D", "", record["aadhaar"]))
        try:
            self.click(self.selectors("verify"))
        except Exception:
            raise
        body = self.page.locator("body").inner_text(timeout=3000).lower()
        if any(marker in body for marker in ("mismatch", "not matched", "invalid aadhaar", "invalid uan")):
            raise RuntimeError("MEMBER_VALIDATION_MISMATCH: portal identity details did not match.")
        if not self.visible(self.selectors("identity_success", [])) and "verified" not in body:
            raise RuntimeError("MEMBER_VALIDATION_MISMATCH: member identity verification was not confirmed.")
