"""Persistent, company/deputed-company scoped intake switches.

The source systems remain read-only.  These local switches are application
configuration and therefore live in a small SQLite database owned by this
service.  The primary key is the exact ``(company_id, deputed_company_id)``
pair, so changing one scope cannot affect another scope.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path(__file__).resolve().parent / "config" / "scope_intake_settings.sqlite3"
LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _scope(company_id: Any, deputed_company_id: Any) -> tuple[str, str]:
    company = str(company_id or "").strip()
    deputed = str(deputed_company_id or "").strip()
    if not company:
        raise ValueError("company_id is required.")
    if not deputed:
        raise ValueError("deputed_company_id is required.")
    return company, deputed


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS scope_intake_settings (
            company_id TEXT NOT NULL,
            deputed_company_id TEXT NOT NULL,
            api_enabled INTEGER NOT NULL DEFAULT 1,
            excel_enabled INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            PRIMARY KEY (company_id, deputed_company_id)
        )
        """
    )
    connection.commit()
    return connection


def _public(row: sqlite3.Row | dict[str, Any], company_id: str, deputed_company_id: str) -> dict[str, Any]:
    return {
        "company_id": company_id,
        "deputed_company_id": deputed_company_id,
        "api_enabled": bool(row["api_enabled"] if isinstance(row, sqlite3.Row) else row.get("api_enabled", 1)),
        "excel_enabled": False,
        "updated_at": row["updated_at"] if isinstance(row, sqlite3.Row) else row.get("updated_at", ""),
        "updated_by": row["updated_by"] if isinstance(row, sqlite3.Row) else row.get("updated_by", ""),
        "storage": "sqlite",
    }


def get(company_id: Any, deputed_company_id: Any) -> dict[str, Any]:
    company, deputed = _scope(company_id, deputed_company_id)
    with LOCK:
        connection = _connect()
        try:
            row = connection.execute(
                "SELECT * FROM scope_intake_settings WHERE company_id = ? AND deputed_company_id = ?",
                (company, deputed),
            ).fetchone()
            if row is None:
                return {
                    "company_id": company,
                    "deputed_company_id": deputed,
                    "api_enabled": True,
                    "excel_enabled": False,
                    "updated_at": "",
                    "updated_by": "default",
                    "storage": "sqlite",
                }
            return _public(row, company, deputed)
        finally:
            connection.close()


def save(company_id: Any, deputed_company_id: Any, *, api_enabled: bool, excel_enabled: bool, updated_by: str) -> dict[str, Any]:
    company, deputed = _scope(company_id, deputed_company_id)
    timestamp = _now()
    with LOCK:
        connection = _connect()
        try:
            connection.execute(
                """
                INSERT INTO scope_intake_settings
                    (company_id, deputed_company_id, api_enabled, excel_enabled, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, deputed_company_id) DO UPDATE SET
                    api_enabled = excluded.api_enabled,
                    excel_enabled = excluded.excel_enabled,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (company, deputed, int(bool(api_enabled)), 0, timestamp, str(updated_by or "system")),
            )
            connection.commit()
            return get(company, deputed)
        finally:
            connection.close()


def list_for_companies(company_ids: set[str] | None = None) -> list[dict[str, Any]]:
    with LOCK:
        connection = _connect()
        try:
            rows = connection.execute("SELECT * FROM scope_intake_settings ORDER BY company_id, deputed_company_id").fetchall()
            result = []
            for row in rows:
                if company_ids is not None and "*" not in company_ids and row["company_id"] not in company_ids:
                    continue
                result.append(_public(row, row["company_id"], row["deputed_company_id"]))
            return result
        finally:
            connection.close()

