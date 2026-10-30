"""Exercise the live-form branch against a local EPFO-shaped page."""

from pathlib import Path

import pytest

from PF.backend.epfo_bot import EpfoBot
from Platform.backend.automation.playwright_page import PlaywrightPage, create_playwright_browser


@pytest.mark.parametrize(
    ("save_message", "save_class", "expected_status"),
    [
        ("Member registered successfully", "alert-success", "Registered - Pending Approval"),
        ("Member is already active in current establishment.", "save-notice", "Blocked - Existing Active Member"),
    ],
)
def test_live_registration_branch_reaches_and_classifies_save(
    tmp_path: Path, save_message: str, save_class: str, expected_status: str,
):
    html = """
      <div id="employeeEnrollmetModal"><button onclick="this.parentElement.style.display='none'">OK</button></div>
      <a href="#" onclick="document.querySelector('#registerLink').style.display='block'">Member</a>
      <a id="registerLink" href="#" style="display:none" onclick="document.querySelector('#form').style.display='block'">REGISTER-INDIVIDUAL</a>
      <div id="form" style="display:none">
        <input id="previousEmployementYes" type="radio" name="previous" onchange="document.querySelector('#identity').style.display='block'">
        <div id="identity" style="display:none">
          <input id="uan"><input id="aadharVerify"><input id="nameVerify"><input id="dobVerify">
        </div>
        <input id="aadhaarConsentChkBox" type="checkbox">
        <input class="btn-verify" type="button" value="Verify" onclick="document.querySelector('#verified').style.display='block'">
        <div id="verified" class="modal" style="display:none">
          Member details matched: Test Employee 19/09/1997 Male Aadhaar ending 0123
          <button onclick="this.parentElement.style.display='none'">Ok</button>
        </div>
        <select id="maritalStatus"><option value="Un-Married" selected>Un-Married</option></select>
        <input id="doj"><input id="wages">
        <button id="savePreviousMemberButton" type="button" onclick="document.querySelector('#saveResult').textContent='SAVE_RESPONSE'">Save</button>
        <div id="saveResult" class="SAVE_CLASS"></div>
      </div>
    """.replace("SAVE_RESPONSE", save_message).replace("SAVE_CLASS", save_class)
    record = {
        "row_number": "1", "name": "Test Employee", "uan": "100000000001",
        "aadhaar": "234567890123", "dob": "19-09-1997", "doj": "26-10-2026",
        "basic_wage": "15000", "gender": "Male",
    }
    browser = create_playwright_browser(headless=True)
    try:
        browser.page.set_content(html)
        bot = EpfoBot(PlaywrightPage(browser.page), lambda _message: None, tmp_path / "artifacts")
        bot.capture_evidence = lambda *_args: ""
        bot._save_error_screenshot = lambda *_args: ""

        result = bot.register_one(record)

        assert result.status == expected_status
        assert browser.page.locator("#dobVerify").input_value() == "19/09/1997"
        assert browser.page.locator("#doj").input_value() == "26/10/2026"
        assert browser.page.locator("#wages").input_value() == record["basic_wage"]
        assert browser.page.locator("#saveResult").inner_text() == save_message
    finally:
        browser.close()
