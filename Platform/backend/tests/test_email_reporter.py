from __future__ import annotations

from email.message import EmailMessage

from reporting.email_reporter import send_failure_email


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: int):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = False
        self.messages: list[EmailMessage] = []
        self.__class__.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = username == "automation@example.com" and password == "secret"

    def send_message(self, message: EmailMessage) -> None:
        self.messages.append(message)


def test_failure_email_contains_rows_and_unique_screenshot(monkeypatch, tmp_path):
    screenshot = tmp_path / "row-7.png"
    screenshot.write_bytes(b"fake-png")
    monkeypatch.setenv("EPFO_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EPFO_SMTP_PORT", "587")
    monkeypatch.setenv("EPFO_SMTP_USER", "automation@example.com")
    monkeypatch.setenv("EPFO_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("EPFO_EMAIL_FROM", "automation@example.com")
    monkeypatch.setenv("EPFO_EMAIL_TO", "hr@example.com;ops@example.com")
    monkeypatch.setenv("EPFO_SMTP_TLS", "1")
    monkeypatch.setattr("reporting.email_reporter.smtplib.SMTP", FakeSMTP)
    FakeSMTP.instances.clear()

    result = send_failure_email(
        [
            {
                "row": "7",
                "employee_name": "Priya",
                "employee_code": "TEST007",
                "uan": "100000000007",
                "status": "Failed",
                "error_type": "PORTAL_LAYOUT_CHANGE",
                "category": "PORTAL_LAYOUT_CHANGE",
                "workflow_step": "Registration form",
                "message": "Save control was not found",
                "screenshot": str(screenshot),
                "evidence_paths": [str(screenshot)],
            }
        ],
        "run-123",
        lambda _: None,
    )

    assert result is True
    smtp = FakeSMTP.instances[0]
    assert smtp.started_tls is True
    assert smtp.logged_in is True
    assert len(smtp.messages) == 1
    message = smtp.messages[0]
    assert message["To"] == "hr@example.com, ops@example.com"
    assert "Source row 7" in message.get_body(preferencelist=("plain",)).get_content()
    assert [part.get_filename() for part in message.iter_attachments()] == ["row-7.png"]


def test_failure_email_does_not_claim_delivery_without_smtp_config(monkeypatch):
    for key in ("EPFO_SMTP_HOST", "EPFO_EMAIL_TO", "EPFO_EMAIL_FROM", "EPFO_SMTP_USER"):
        monkeypatch.delenv(key, raising=False)
    messages: list[str] = []

    result = send_failure_email(
        [{"row": "2", "status": "Validation Failed", "message": "Bad mobile"}],
        "run-no-smtp",
        messages.append,
    )

    assert result is False
    assert "not sent" in messages[0]
