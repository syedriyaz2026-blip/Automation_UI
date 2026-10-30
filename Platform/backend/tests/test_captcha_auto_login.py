import base64
import io
from pathlib import Path
from urllib.parse import quote

import cv2
import numpy as np
from PIL import Image

from PF.backend.captcha_solver import CaptchaReadError, _read_captcha_rapidocr, read_captcha
from PF.backend.epfo_bot import EpfoBot
from Platform.backend.authentication import AuthState
from Platform.backend.automation.playwright_page import PlaywrightPage, create_playwright_browser


def test_captcha_solver_orders_vertically_offset_glyphs(monkeypatch):
    image = np.full((50, 150), 255, dtype="uint8")
    for index, width in enumerate(range(4, 9)):
        x = 15 + index * 24
        y = 5 + (index % 3) * 9
        image[y:y + 12, x:x + width] = 0
    _, encoded = cv2.imencode(".png", image)

    class FakeOCR:
        def __call__(self, crop, **_kwargs):
            width = (crop < 100).any(axis=0).sum() // 3
            return type("Prediction", (), {"txts": ("ABCDE"[width - 4],), "scores": (0.99,)})()

    monkeypatch.setattr("PF.backend.captcha_solver._ocr_engine", lambda: FakeOCR())
    assert _read_captcha_rapidocr(encoded.tobytes()) == "ABCDE"


def test_captcha_solver_rejects_wrong_character_count():
    image = np.full((50, 150), 255, dtype="uint8")
    image[10:25, 20:30] = 0
    _, encoded = cv2.imencode(".png", image)
    try:
        read_captcha(encoded.tobytes())
    except CaptchaReadError as exc:
        assert "count" in str(exc)
    else:
        raise AssertionError("Incomplete challenge should not be submitted")


def _image_data(color):
    image = Image.new("RGB", (150, 50), color)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def test_real_auth_flow_fills_captcha_clicks_sign_in_and_retries(tmp_path: Path, monkeypatch):
    first_image = _image_data("white")
    second_image = _image_data("lightgray")
    html = f"""
      <form id="login" onsubmit="event.preventDefault(); window.attempts += 1;
        if (document.querySelector('#captcha').value === 'AB123') {{
          document.body.innerHTML = '<h1>Dashboard</h1>';
        }} else {{
          document.querySelector('img.captcha-img').src = '{second_image}';
        }}">
        <input name="username"><input name="password" type="password">
        <img class="captcha-img" src="{first_image}">
        <input id="captcha" name="captcha" maxlength="5">
        <button type="submit">Sign In</button>
      </form><script>window.attempts = 0;</script>
    """
    values = iter(("WRONG", "AB123"))
    monkeypatch.setattr("PF.backend.epfo_bot.read_captcha", lambda _image: next(values))
    browser = create_playwright_browser(headless=True)
    try:
        page = PlaywrightPage(browser.page)
        bot = EpfoBot(page, lambda _message: None, tmp_path / "artifacts")
        bot.settings["auth_mode"] = "REAL"
        bot.settings["polling_interval_ms"] = 200
        monkeypatch.setattr(bot, "capture_evidence", lambda *_args: "")
        bot.login_page("employer", "password", "data:text/html," + quote(html))
        result = bot.authenticate()
        assert result.state == AuthState.REGISTRATION_READY
        assert browser.page.evaluate("window.attempts") == 2
    finally:
        browser.close()
