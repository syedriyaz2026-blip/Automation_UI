from pathlib import Path

from captcha_service import CaptchaDetector, CaptchaStatus, CaptchaType, DetectionStatus


class FakeLocator:
    def __init__(self, visible=True, image=b"captcha-a"):
        self.visible = visible
        self.image = image

    def is_visible(self, timeout=0):
        return self.visible

    def screenshot(self, path=None, type="png"):
        if path:
            Path(path).write_bytes(self.image)
            return None
        return self.image


class FakePage:
    def __init__(self, locator=None):
        self.locator_value = locator
        self.screenshots = []

    def locator(self, _selector):
        class Wrapper:
            def __init__(self, value):
                self.value = value

            @property
            def first(self):
                if self.value is None:
                    raise RuntimeError("missing")
                return self.value

        return Wrapper(self.locator_value)

def detector(tmp_path, locator):
    return CaptchaDetector(FakePage(locator), lambda: ["img.captcha"], tmp_path)


def test_no_captcha_is_not_present(tmp_path):
    observation = detector(tmp_path, None).observe()
    assert observation.status == CaptchaStatus.NOT_PRESENT
    assert observation.captcha_type == CaptchaType.NONE


def test_image_captcha_is_detected_and_evidence_is_saved(tmp_path):
    observation = detector(tmp_path, FakeLocator()).observe()
    assert observation.status == CaptchaStatus.PRESENT
    assert observation.captcha_type == CaptchaType.IMAGE
    assert observation.fingerprint
    assert Path(observation.evidence_path).exists()
    assert observation.public_dict().keys() == {"status", "captcha_type", "fingerprint", "evidence_path", "message", "observed_at"}


def test_changed_image_is_detected_without_reading_text(tmp_path):
    page = FakePage(FakeLocator(image=b"first"))
    detector_instance = CaptchaDetector(page, lambda: ["img.captcha"], tmp_path)
    assert detector_instance.observe(capture=False).status == CaptchaStatus.PRESENT
    page.locator_value.image = b"second"
    assert detector_instance.observe(capture=False).status == CaptchaStatus.CHANGED


def test_expiry_is_explicit_and_safe(tmp_path):
    observation = detector(tmp_path, FakeLocator()).mark_expired()
    assert observation.status == CaptchaStatus.EXPIRED
    assert "captcha" in observation.message.lower()
    assert not hasattr(observation, "answer")


def test_structured_classification_detects_captcha(tmp_path):
    detector_instance = CaptchaDetector(FakePage(FakeLocator()), lambda: ["img.captcha"], tmp_path)
    assert detector_instance.classify() == DetectionStatus.CAPTCHA_DETECTED
    assert detector_instance.classify_result()["classification"] == "CAPTCHA_DETECTED"


def test_structured_classification_detects_gateway_block(tmp_path):
    page = FakePage(FakeLocator())
    page.access_blocked = lambda: True
    detector_instance = CaptchaDetector(page, lambda: ["img.captcha"], tmp_path)
    assert detector_instance.classify() == DetectionStatus.GATEWAY_BLOCKED
