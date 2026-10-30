from __future__ import annotations

import api_service


class FakeStore:
    def __init__(self, run):
        self.run = run
        self.events = []

    def get(self, run_id):
        return self.run if run_id == self.run["run_id"] else None

    def event(self, run_id, event, status, **extra):
        self.events.append((run_id, event, status, extra))


def test_handover_email_retry_only_sends_notification(monkeypatch):
    run = {
        "run_id": "RUN-EMAIL-1",
        "status": "HANDOVER_REQUIRED",
        "handover": {"status": "PENDING_MANUAL_HANDOVER"},
        "results": [{"row": "7", "status": "Failed", "message": "Layout changed"}],
    }
    store = FakeStore(run)
    calls = []
    monkeypatch.setattr(api_service, "STORE", store)
    monkeypatch.setattr(api_service, "send_failure_email", lambda results, run_id, log: calls.append((results, run_id)) or True)

    result = api_service.attempt_handover_email("RUN-EMAIL-1", force=True)

    assert result == {"attempted": True, "sent": True}
    assert len(calls) == 1
    assert run["handover_email"] == result
    assert store.events[0][1] == "HANDOVER_EMAIL_SENT"
    assert run["results"][0]["status"] == "Failed"


def test_handover_email_retry_does_not_resend_after_success(monkeypatch):
    run = {
        "run_id": "RUN-EMAIL-2",
        "status": "HANDOVER_REQUIRED",
        "handover": {"status": "PENDING_MANUAL_HANDOVER"},
        "handover_email": {"attempted": True, "sent": True},
        "results": [],
    }
    store = FakeStore(run)
    calls = []
    monkeypatch.setattr(api_service, "STORE", store)
    monkeypatch.setattr(api_service, "send_failure_email", lambda *args: calls.append(args) or True)

    result = api_service.attempt_handover_email("RUN-EMAIL-2", force=True)

    assert result["sent"] is True
    assert calls == []
    assert store.events == []
