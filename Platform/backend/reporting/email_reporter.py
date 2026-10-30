"""SMTP notification for failed rows and their local evidence."""

from __future__ import annotations

import mimetypes
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable


def _split_addresses(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def _value(row: dict[str, Any], lower: str, upper: str = "") -> str:
    return str(row.get(lower, row.get(upper, "")) or "")


def _evidence_paths(row: dict[str, Any]) -> list[Path]:
    values: list[Any] = [
        row.get("screenshot"),
        row.get("Screenshot/Evidence"),
        row.get("evidence"),
        row.get("evidence_paths"),
    ]
    paths: list[Path] = []
    for value in values:
        candidates = value if isinstance(value, (list, tuple, set)) else [value]
        for candidate in candidates:
            if candidate:
                paths.append(Path(str(candidate)))
    return paths


def send_failure_email(
    results: list[dict[str, Any]],
    run_id: str,
    log: Callable[[str], None],
) -> bool:
    """Send one consolidated failure email and return whether SMTP accepted it.

    Missing SMTP configuration is a safe, explicit non-send; it is never
    reported as a successful delivery.
    """

    success_status = "Registered - Pending Approval"
    failures = [row for row in results if _value(row, "status", "Status") not in ("", success_status)]
    if not failures:
        return False

    host = os.getenv("EPFO_SMTP_HOST", "").strip()
    recipients = _split_addresses(os.getenv("EPFO_EMAIL_TO", ""))
    sender = os.getenv("EPFO_EMAIL_FROM", "").strip() or os.getenv("EPFO_SMTP_USER", "").strip()
    if not host or not recipients or not sender:
        log("Failure email not sent: configure EPFO_SMTP_HOST, EPFO_EMAIL_TO, and EPFO_EMAIL_FROM (or EPFO_SMTP_USER).")
        return False

    try:
        port = int(os.getenv("EPFO_SMTP_PORT", "587"))
    except ValueError as exc:
        raise ValueError("EPFO_SMTP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("EPFO_SMTP_PORT must be between 1 and 65535")

    username = os.getenv("EPFO_SMTP_USER", "").strip()
    password = os.getenv("EPFO_SMTP_PASSWORD", "")
    cc = _split_addresses(os.getenv("EPFO_EMAIL_CC", ""))
    use_ssl = os.getenv("EPFO_SMTP_SSL", "0").strip().lower() in {"1", "true", "yes"}
    use_tls = os.getenv("EPFO_SMTP_TLS", "1").strip().lower() in {"1", "true", "yes"}
    if use_ssl and use_tls:
        use_tls = False

    msg = EmailMessage()
    msg["Subject"] = f"EPFO automation errors — {run_id} — {len(failures)} row(s)"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    if cc:
        msg["Cc"] = ", ".join(cc)

    lines = [
        "EPFO automation stopped before approval.",
        f"Run ID: {run_id}",
        f"Failed rows: {len(failures)}",
        "",
    ]
    for item in failures:
        lines.append(
            f"Source row {_value(item, 'row', 'Source Row')}: "
            f"{_value(item, 'employee_name', 'Employee Name')} | "
            f"Employee code {_value(item, 'employee_code', 'Employee Code')} | "
            f"UAN {_value(item, 'uan', 'UAN')} | "
            f"{_value(item, 'error_type', 'Error Type')} | "
            f"category {_value(item, 'category', 'Category')} | "
            f"step: {_value(item, 'workflow_step', 'Workflow Step')} | "
            f"{_value(item, 'message', 'Message')}"
        )

    attachment_paths: list[Path] = []
    seen: set[str] = set()
    missing: list[str] = []
    for item in failures:
        for path in _evidence_paths(item):
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen:
                continue
            seen.add(key)
            if path.exists() and path.is_file():
                attachment_paths.append(path)
            else:
                missing.append(str(path))

    lines.extend(["", f"Evidence attachments: {len(attachment_paths)}"])
    if missing:
        lines.append(f"Evidence paths unavailable locally: {len(missing)}")
    if not attachment_paths:
        lines.append("No local screenshot/evidence file was available to attach.")
    msg.set_content("\n".join(lines))

    for path in attachment_paths:
        mime, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (mime or "application/octet-stream").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)

    smtp_factory = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_factory(host, port, timeout=30) as smtp:
        if use_tls:
            smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(msg)

    log(f"Failure email sent to {', '.join(recipients)}; attached {len(attachment_paths)} evidence file(s).")
    return True
