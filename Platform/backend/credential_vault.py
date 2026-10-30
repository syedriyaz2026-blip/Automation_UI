"""Small, local-only encrypted credential vault for company scopes.

Windows DPAPI encrypts the values for the current Windows user and machine. The
vault is intentionally local and never returns passwords through the API.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sqlite3
import threading
from ctypes import POINTER, Structure, byref, c_ubyte, cast, string_at, wintypes
from pathlib import Path
from typing import Any


LOCK = threading.RLock()


class _DataBlob(Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", POINTER(c_ubyte))]


class CredentialVault:
    """Persist scoped credentials in SQLite with Windows user-bound DPAPI.

    The ciphertext is stored in the local portal-management database; the
    plaintext password never leaves this process and is never returned by an
    API response.  The legacy JSON file is read once as a migration source so
    existing local credentials are not lost.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = self.path.with_name("portal_management.sqlite3")
        if os.name != "nt":
            raise RuntimeError("The local credential vault requires Windows DPAPI.")
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32
        self._ensure_database()

    def _protect(self, value: bytes) -> bytes:
        source_buffer = (c_ubyte * len(value)).from_buffer_copy(value)
        source = _DataBlob(len(value), cast(source_buffer, POINTER(c_ubyte)))
        target = _DataBlob()
        if not self._crypt32.CryptProtectData(
            byref(source), None, None, None, None, 0, byref(target)
        ):
            raise ctypes.WinError()
        try:
            return string_at(target.pbData, target.cbData)
        finally:
            self._kernel32.LocalFree(target.pbData)

    def _unprotect(self, value: bytes) -> bytes:
        source_buffer = (c_ubyte * len(value)).from_buffer_copy(value)
        source = _DataBlob(len(value), cast(source_buffer, POINTER(c_ubyte)))
        target = _DataBlob()
        if not self._crypt32.CryptUnprotectData(
            byref(source), None, None, None, None, 0, byref(target)
        ):
            raise ctypes.WinError()
        try:
            return string_at(target.pbData, target.cbData)
        finally:
            self._kernel32.LocalFree(target.pbData)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _ensure_database(self) -> None:
        with LOCK:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS portal_credentials (
                        scope_key TEXT PRIMARY KEY,
                        ciphertext TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS esi_accounts (
                        account_key TEXT PRIMARY KEY,
                        client_id TEXT NOT NULL,
                        client_name TEXT NOT NULL DEFAULT '',
                        state TEXT NOT NULL DEFAULT '',
                        district TEXT NOT NULL DEFAULT '',
                        ciphertext TEXT NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                existing = connection.execute("SELECT COUNT(*) FROM portal_credentials").fetchone()[0]
                if not existing and self.path.exists():
                    try:
                        payload = json.loads(self.path.read_text(encoding="utf-8"))
                        records = payload.get("records", {})
                    except (OSError, ValueError, TypeError, json.JSONDecodeError):
                        records = {}
                    for scope_key, ciphertext in records.items():
                        if isinstance(scope_key, str) and isinstance(ciphertext, str):
                            connection.execute(
                                "INSERT OR IGNORE INTO portal_credentials(scope_key, ciphertext, updated_at) VALUES (?, ?, datetime('now'))",
                                (scope_key, ciphertext),
                            )
                connection.commit()
            finally:
                connection.close()

    def _read(self) -> dict[str, str]:
        with LOCK:
            connection = self._connect()
            try:
                rows = connection.execute("SELECT scope_key, ciphertext FROM portal_credentials").fetchall()
                return {str(key): str(ciphertext) for key, ciphertext in rows}
            finally:
                connection.close()

    def _write(self, records: dict[str, str]) -> None:
        with LOCK:
            connection = self._connect()
            try:
                connection.execute("DELETE FROM portal_credentials")
                connection.executemany(
                    "INSERT INTO portal_credentials(scope_key, ciphertext, updated_at) VALUES (?, ?, datetime('now'))",
                    [(key, encoded) for key, encoded in records.items()],
                )
                connection.commit()
            finally:
                connection.close()

    def get(self, scope_key: str) -> dict[str, str] | None:
        records = self._read()
        encoded = records.get(scope_key)
        if not encoded:
            return None
        try:
            value: Any = json.loads(
                self._unprotect(base64.b64decode(encoded)).decode("utf-8")
            )
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict) or not value.get("username") or not value.get("password"):
            return None
        return {"username": str(value["username"]), "password": str(value["password"])}

    def set(self, scope_key: str, username: str, password: str) -> None:
        records = self._read()
        raw = json.dumps({"username": username, "password": password}).encode("utf-8")
        records[scope_key] = base64.b64encode(self._protect(raw)).decode("ascii")
        self._write(records)

    @staticmethod
    def esi_account_key(client_id: str, state: str = "", district: str = "") -> str:
        """Return the stable, scope-specific ESIC account key."""
        return "esi:{client}:{state}:{district}".format(
            client=str(client_id or "").strip(),
            state=str(state or "").strip().casefold(),
            district=str(district or "").strip().casefold(),
        )

    def set_esi_account(
        self,
        client_id: str,
        state: str,
        district: str,
        username: str,
        password: str,
        *,
        client_name: str = "",
        active: bool = True,
    ) -> dict[str, Any]:
        """Save an encrypted ESIC account and return metadata only."""
        if not str(client_id or "").strip() or not username or not password:
            raise ValueError("client_id, username, and password are required for an ESI account.")
        account_key = self.esi_account_key(client_id, state, district)
        raw = json.dumps({"username": str(username), "password": str(password)}).encode("utf-8")
        encoded = base64.b64encode(self._protect(raw)).decode("ascii")
        metadata = {
            "account_key": account_key,
            "client_id": str(client_id).strip(),
            "client_name": str(client_name or "").strip(),
            "state": str(state or "").strip(),
            "district": str(district or "").strip(),
            "active": bool(active),
        }
        with LOCK:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO esi_accounts(account_key, client_id, client_name, state, district, ciphertext, active, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(account_key) DO UPDATE SET
                        client_id=excluded.client_id,
                        client_name=excluded.client_name,
                        state=excluded.state,
                        district=excluded.district,
                        ciphertext=excluded.ciphertext,
                        active=excluded.active,
                        updated_at=datetime('now')
                    """,
                    (account_key, metadata["client_id"], metadata["client_name"], metadata["state"], metadata["district"], encoded, 1 if active else 0),
                )
                connection.commit()
            finally:
                connection.close()
        return metadata

    def get_esi_account(self, client_id: str, state: str = "", district: str = "") -> dict[str, str] | None:
        account_key = self.esi_account_key(client_id, state, district)
        with LOCK:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT ciphertext, active FROM esi_accounts WHERE account_key=?",
                    (account_key,),
                ).fetchone()
            finally:
                connection.close()
        if not row or not int(row[1]):
            return None
        try:
            value: Any = json.loads(self._unprotect(base64.b64decode(str(row[0]))).decode("utf-8"))
        except (ValueError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(value, dict) or not value.get("username") or not value.get("password"):
            return None
        return {"username": str(value["username"]), "password": str(value["password"])}

    def list_esi_account_metadata(self, *, client_id: str = "") -> list[dict[str, Any]]:
        with LOCK:
            connection = self._connect()
            try:
                if client_id:
                    rows = connection.execute(
                        "SELECT account_key, client_id, client_name, state, district, active, updated_at FROM esi_accounts WHERE client_id=? ORDER BY client_name, state, district",
                        (str(client_id),),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT account_key, client_id, client_name, state, district, active, updated_at FROM esi_accounts ORDER BY client_name, state, district",
                    ).fetchall()
            finally:
                connection.close()
        return [
            {
                "account_key": str(row[0]),
                "client_id": str(row[1]),
                "client_name": str(row[2] or ""),
                "state": str(row[3] or ""),
                "district": str(row[4] or ""),
                "active": bool(row[5]),
                "updated_at": str(row[6]),
                "password_configured": bool(row[5]),
            }
            for row in rows
        ]

    def set_esi_account_active(self, account_key: str, active: bool) -> bool:
        with LOCK:
            connection = self._connect()
            try:
                cursor = connection.execute(
                    "UPDATE esi_accounts SET active=?, updated_at=datetime('now') WHERE account_key=?",
                    (1 if active else 0, str(account_key)),
                )
                connection.commit()
                return cursor.rowcount > 0
            finally:
                connection.close()
