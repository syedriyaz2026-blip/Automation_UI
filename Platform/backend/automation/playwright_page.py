"""Playwright page adapter used by the supervised PF worker."""
from __future__ import annotations

import time
from pathlib import Path
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright


class PlaywrightLocator:
    def __init__(self, locator):
        self._locator = locator

    @property
    def first(self):
        return PlaywrightLocator(self._locator.first)

    def wait_for(self, state="visible", timeout=2500):
        return self._locator.wait_for(state=state, timeout=timeout)

    def is_visible(self, timeout=500):
        try:
            return self._locator.is_visible(timeout=timeout)
        except Exception:
            return False

    def fill(self, value):
        return self._locator.fill(str(value))

    def input_value(self):
        return self._locator.input_value()

    def type(self, value, delay=0):
        self._locator.fill("")
        return self._locator.press_sequentially(str(value), delay=delay)

    def click(self, timeout=2500):
        return self._locator.click(timeout=timeout)

    def check(self):
        return self._locator.check()

    def is_checked(self):
        return self._locator.is_checked()

    def inner_text(self, timeout=3000):
        return self._locator.inner_text(timeout=timeout)

    def evaluate(self, script):
        return self._locator.evaluate(script)

    def screenshot(self, path=None, type="png"):
        return self._locator.screenshot(path=str(path) if path else None, type=type)


class PlaywrightPage:
    def __init__(self, page):
        self.page = page

    @property
    def url(self):
        return self.page.url

    def locator(self, selector):
        return PlaywrightLocator(self.page.locator(selector))

    def goto(self, url, **kwargs):
        return self.page.goto(url, wait_until=kwargs.get("wait_until", "domcontentloaded"))

    def reload(self, **kwargs):
        return self.page.reload(wait_until=kwargs.get("wait_until", "domcontentloaded"))

    def screenshot(self, path, full_page=False):
        return self.page.screenshot(path=str(path), full_page=full_page)

    def wait_for_timeout(self, ms):
        return self.page.wait_for_timeout(ms)

    def add_style_tag(self, content):
        return self.page.add_style_tag(content=content)

    def set_page_zoom(self, factor: float = 0.5):
        factor = max(0.25, min(float(factor), 2.0))
        self.page.evaluate("(factor) => { document.documentElement.style.zoom = factor; }", factor)

    def body_text(self):
        try:
            return self.page.locator("body").inner_text().lower()
        except Exception:
            return ""

    def access_blocked(self):
        text = self.body_text()
        return any(marker in text for marker in ("web page blocked", "access denied", "request blocked", "forbidden", "security policy"))

    def wait_for_url(self, *args, **kwargs):
        return self.page.wait_for_url(*args, **kwargs)

    def get_by_role(self, role, name, exact=True):
        return PlaywrightLocator(self.page.get_by_role(role, name=name, exact=exact))

    def get_by_label(self, name):
        return PlaywrightLocator(self.page.get_by_label(name))

    def get_by_text(self, text, exact=False):
        return PlaywrightLocator(self.page.get_by_text(text, exact=exact))

    def once(self, event, handler):
        return self.page.once(event, handler)


class PlaywrightBrowser:
    def __init__(self, playwright, browser, page):
        self._playwright = playwright
        self._browser = browser
        self.page = page

    def close(self):
        try:
            self._browser.close()
        finally:
            self._playwright.stop()


def create_playwright_browser(*, headless=False, width=1440, height=900):
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless, channel="msedge" if not headless else None)
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    return PlaywrightBrowser(pw, browser, page)

