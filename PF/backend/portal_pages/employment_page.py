from __future__ import annotations

from .base_page import PortalPage


class EmploymentPage(PortalPage):
    """Employment and optional employee fields, independent of identity flow."""

    def fill_details(self, record):
        self.assert_contract("registration_details")
        self.fill(self.selectors("joining_date"), record["doj"])
        self.fill(self.selectors("wage"), record["basic_wage"])
        fields = {
            "mobile": ["input[name*='mobile' i]", "input[id*='mobile' i]"],
            "email": ["input[name*='email' i]", "input[id*='email' i]"],
            "father_name": ["input[name*='father' i]", "input[id*='father' i]"],
        }
        for key, selectors in fields.items():
            if record.get(key):
                try:
                    self.fill(selectors, record[key])
                except Exception as exc:
                    raise RuntimeError(f"Field unavailable or not editable: {key} ({exc})") from exc
