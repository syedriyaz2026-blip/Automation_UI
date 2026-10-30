from authentication import AuthState, MockAuthenticationProvider
from epfo_bot import EpfoBot


class FakeLocator:
    def __init__(self, visible=False):
        self.visible = visible

    def first(self):
        return self

    def is_visible(self, timeout=0):
        return self.visible


class FakePage:
    def __init__(self, visible=None, text=""):
        self.visible = visible or set()
        self.text = text

    def locator(self, selector):
        class Wrapper:
            def __init__(self, page, selector):
                self.page, self.selector = page, selector
            @property
            def first(self):
                return FakeLocator(self.selector in self.page.visible)
            def inner_text(self, timeout=0):
                return self.page.text
        return Wrapper(self, selector)


def detection_bot(visible=None, text=""):
    bot = object.__new__(EpfoBot)
    bot.page = FakePage(visible, text)
    bot.selectors = {
        "login_password": ["password"], "auth_success": ["dashboard"],
        "captcha": ["captcha"], "session_expired": ["expired"],
        "auth_failure": ["failure"], "registration_ready": ["registration"],
    }
    return bot


def test_portal_state_detection_for_login_captcha_and_dashboard():
    assert EpfoBot._detect_auth_state(detection_bot({"password"})) == AuthState.LOGIN_PAGE
    assert EpfoBot._detect_auth_state(detection_bot({"captcha"})) == AuthState.CAPTCHA_REQUIRED
    assert EpfoBot._detect_auth_state(detection_bot({"dashboard"})) == AuthState.AUTHENTICATED


def test_portal_state_detection_for_failure_and_session_expiry():
    assert EpfoBot._detect_auth_state(detection_bot({"failure"})) == AuthState.AUTHENTICATION_FAILED
    assert EpfoBot._detect_auth_state(detection_bot({"expired"})) == AuthState.SESSION_EXPIRED


def states_for(scenario):
    seen = []
    result = MockAuthenticationProvider(scenario, lambda _message: None).authenticate(
        lambda state, _message: seen.append(state)
    )
    return result, seen


def test_mock_success_reaches_registration_ready():
    result, seen = states_for("SUCCESS")
    assert result.state == AuthState.REGISTRATION_READY
    assert AuthState.AUTHENTICATED in seen


def test_mock_failure_scenarios_never_reach_registration():
    for scenario in ("TIMEOUT", "SESSION_EXPIRED", "AUTHENTICATION_FAILURE"):
        result, seen = states_for(scenario)
        assert result.state != AuthState.REGISTRATION_READY


def test_mock_does_not_accept_unknown_scenario():
    try:
        MockAuthenticationProvider("UNKNOWN_SCENARIO")
    except ValueError as exc:
        assert "Unsupported mock" in str(exc)
    else:
        raise AssertionError("Unknown mock scenario should be rejected")
