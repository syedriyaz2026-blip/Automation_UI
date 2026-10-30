"""Local role and company-assignment store for the control panel."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROLE_SUPER_ADMIN = "super_admin"
ROLE_MASTER_ADMIN = "master_admin"
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = {ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_USER}
STATUTORY_MODULES = ("PF", "ESI", "LWF", "PT")
PERMISSIONS = {
    "companies.view",
    "companies.manage",
    "users.view",
    "users.manage",
    "permissions.manage",
    "reports.view",
    "reports.download",
    "activity.view",
    "credentials.view",
    "credentials.manage",
    "intake.api",
    "intake.excel",
    "intake.settings.view",
    "intake.settings.manage",
    "employee_queue.view",
    "monitoring.view",
    "evidence.view",
    "runs.start",
    "modules.view",
    "modules.manage",
}

# These are safe defaults for newly-created accounts. A Super Admin can
# replace them with an explicit permission set when creating or editing an
# account. The backend always evaluates the persisted permission set.
ROLE_DEFAULT_PERMISSIONS = {
    ROLE_MASTER_ADMIN: set(PERMISSIONS),
    ROLE_SUPER_ADMIN: set(PERMISSIONS),
    ROLE_ADMIN: {
        "companies.view",
        "reports.view",
        "reports.download",
        "activity.view",
        "credentials.view",
        "credentials.manage",
        "intake.api",
        "intake.excel",
        "intake.settings.view",
        "intake.settings.manage",
        "employee_queue.view",
        "monitoring.view",
        "evidence.view",
        "runs.start",
    },
    ROLE_USER: {
        "companies.view",
        "intake.api",
        "intake.excel",
        "intake.settings.view",
        "employee_queue.view",
    },
}
ROLE_DEFAULT_MODULE_ACCESS = {
    ROLE_MASTER_ADMIN: set(STATUTORY_MODULES),
    ROLE_SUPER_ADMIN: set(STATUTORY_MODULES),
    ROLE_ADMIN: {"PF", "ESI"},
    ROLE_USER: {"PF"},
}
CONFIG_ROOT = Path(__file__).resolve().parent / "config"
LEGACY_USERS_PATH = CONFIG_ROOT / "admin_users.json"
USERS_DB_PATH = CONFIG_ROOT / "portal_management.sqlite3"
# Kept as a compatibility constant for callers that used the old JSON store.
USERS_PATH = LEGACY_USERS_PATH
ITERATIONS = 120_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_password(password: str, salt: str | None = None) -> str:
    salt_value = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt_value.encode(), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt_value}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def normalize_permissions(values: Any) -> list[str]:
    """Return a stable, validated permission list for storage and tokens."""
    if values is None:
        return []
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, (list, tuple, set)):
        return []
    return sorted({str(value).strip() for value in values if str(value).strip() in PERMISSIONS})


def default_permissions(role: str) -> list[str]:
    return sorted(ROLE_DEFAULT_PERMISSIONS.get(role, ROLE_DEFAULT_PERMISSIONS[ROLE_USER]))


def permissions_for_user(user: dict[str, Any]) -> list[str]:
    if "permissions" in user:
        return normalize_permissions(user.get("permissions"))
    return default_permissions(str(user.get("role", ROLE_USER)))


def normalize_module_access(values: Any) -> list[str]:
    """Normalize module grants using the stable statutory-module registry."""
    if values is None:
        return []
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, (list, tuple, set)):
        return []
    return sorted({str(value).strip().upper() for value in values if str(value).strip().upper() in STATUTORY_MODULES})


def default_module_access(role: str) -> list[str]:
    return sorted(ROLE_DEFAULT_MODULE_ACCESS.get(role, ROLE_DEFAULT_MODULE_ACCESS[ROLE_USER]))


def module_access_for_user(user: dict[str, Any]) -> list[str]:
    if "module_access" in user:
        return normalize_module_access(user.get("module_access"))
    return default_module_access(str(user.get("role", ROLE_USER)))


class UserStore:
    """Persistent account store.

    The portal-management account database is SQLite so role, permission,
    activation, assignment, and password changes survive process restarts and
    are enforced from the same persisted source by every API request. A
    legacy JSON file is imported once when the database is first created.
    Passing a ``.json`` path remains supported for small isolated tests.
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path or USERS_DB_PATH)
        self.sqlite = self.path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
        self.lock = threading.RLock()
        if self.sqlite:
            self._ensure_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_database(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS portal_users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    company_ids TEXT NOT NULL DEFAULT '[]',
                    permissions TEXT NOT NULL DEFAULT '[]',
                    module_access TEXT NOT NULL DEFAULT '[]',
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(portal_users)").fetchall()}
            added_module_column = False
            if "module_access" not in columns:
                connection.execute("ALTER TABLE portal_users ADD COLUMN module_access TEXT NOT NULL DEFAULT '[]'")
                added_module_column = True
            if added_module_column:
                for row in connection.execute("SELECT id, role FROM portal_users").fetchall():
                    connection.execute(
                        "UPDATE portal_users SET module_access=? WHERE id=?",
                        (json.dumps(default_module_access(str(row[1]))), row[0]),
                    )
            has_users = connection.execute("SELECT 1 FROM portal_users LIMIT 1").fetchone()
            if not has_users and LEGACY_USERS_PATH.exists():
                try:
                    legacy = json.loads(LEGACY_USERS_PATH.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    legacy = {"users": []}
                self._replace_sqlite_users(connection, legacy.get("users", []))
            # The original local bootstrap account is promoted once to the
            # highest role so a fresh installation has a usable Master Admin
            # entry without introducing a second hard-coded password. The
            # account keeps its existing password hash and can create the
            # Super Admin accounts from the portal-management screen.
            has_master = connection.execute(
                "SELECT 1 FROM portal_users WHERE role=? LIMIT 1",
                (ROLE_MASTER_ADMIN,),
            ).fetchone()
            if not has_master:
                bootstrap = connection.execute(
                    "SELECT id FROM portal_users WHERE id=? OR username=? ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END LIMIT 1",
                    ("local-super-admin", "A", "local-super-admin"),
                ).fetchone()
                if bootstrap:
                    connection.execute(
                        "UPDATE portal_users SET role=?, display_name=?, permissions=?, module_access=?, updated_at=? WHERE id=?",
                        (
                            ROLE_MASTER_ADMIN,
                            "Master Admin",
                            json.dumps(default_permissions(ROLE_MASTER_ADMIN)),
                            json.dumps(default_module_access(ROLE_MASTER_ADMIN)),
                            utc_now(),
                            bootstrap[0],
                        ),
                    )

    @staticmethod
    def _replace_sqlite_users(connection: sqlite3.Connection, users: list[dict[str, Any]]) -> None:
        connection.execute("DELETE FROM portal_users")
        for user in users:
            connection.execute(
                """
                INSERT INTO portal_users
                    (id, username, display_name, role, active, company_ids,
                     permissions, module_access, password_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(user.get("id") or uuid.uuid4()),
                    str(user.get("username", "")),
                    str(user.get("display_name") or user.get("username", "")),
                    str(user.get("role", ROLE_USER)),
                    1 if user.get("active", True) else 0,
                    json.dumps(list(user.get("company_ids", []))),
                    json.dumps(permissions_for_user(user)),
                    json.dumps(module_access_for_user(user)),
                    str(user.get("password_hash", "")),
                    str(user.get("created_at") or utc_now()),
                    str(user.get("updated_at") or utc_now()),
                ),
            )

    @staticmethod
    def _sqlite_user(row: sqlite3.Row) -> dict[str, Any]:
        def load_array(value: Any) -> list[Any]:
            try:
                loaded = json.loads(value or "[]")
            except (TypeError, json.JSONDecodeError):
                return []
            return loaded if isinstance(loaded, list) else []

        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "active": bool(row["active"]),
            "company_ids": load_array(row["company_ids"]),
            "permissions": load_array(row["permissions"]),
            "module_access": load_array(row["module_access"]),
            "password_hash": row["password_hash"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _read(self) -> dict[str, Any]:
        if self.sqlite:
            self._ensure_database()
            with self._connect() as connection:
                rows = connection.execute("SELECT * FROM portal_users ORDER BY username").fetchall()
            return {"users": [self._sqlite_user(row) for row in rows]}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"users": []}

    def _write(self, payload: dict[str, Any]) -> None:
        if self.sqlite:
            self._ensure_database()
            with self._connect() as connection:
                self._replace_sqlite_users(connection, payload.get("users", []))
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        with self.lock:
            for user in self._read().get("users", []):
                if user.get("username") == username and user.get("active", True) and verify_password(password, user.get("password_hash", "")):
                    return self.public_user(user)
        return None

    @staticmethod
    def public_user(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user.get("id", ""),
            "username": user.get("username", ""),
            "display_name": user.get("display_name", user.get("username", "")),
            "role": user.get("role", ROLE_USER),
            "active": bool(user.get("active", True)),
            "company_ids": list(user.get("company_ids", [])),
            "permissions": permissions_for_user(user),
            "module_access": module_access_for_user(user),
            "created_at": user.get("created_at", ""),
            "updated_at": user.get("updated_at", ""),
        }

    def list_users(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self.public_user(item) for item in self._read().get("users", [])]

    def find(self, user_id: str) -> dict[str, Any] | None:
        with self.lock:
            return next((item for item in self._read().get("users", []) if item.get("id") == user_id or item.get("username") == user_id), None)

    def upsert(self, payload: dict[str, Any], user_id: str = "") -> dict[str, Any]:
        username = str(payload.get("username", "")).strip()
        role = str(payload.get("role", ROLE_USER)).strip().lower()
        if not username or role not in ROLES:
            raise ValueError("username and a valid role are required.")
        with self.lock:
            data = self._read()
            users = data.setdefault("users", [])
            existing = next((item for item in users if item.get("id") == user_id or item.get("username") == username), None)
            if existing is None:
                if any(item.get("username") == username for item in users):
                    raise ValueError("That username already exists.")
                password = str(payload.get("password", ""))
                if not password:
                    raise ValueError("A password is required for a new account.")
                existing = {"id": str(uuid.uuid4()), "username": username, "created_at": utc_now()}
                users.append(existing)
            elif username != existing.get("username") and any(item.get("username") == username for item in users):
                raise ValueError("That username already exists.")
            existing.update({
                "username": username,
                "display_name": str(payload.get("display_name", username)).strip() or username,
                "role": role,
                "active": bool(payload.get("active", True)),
                "company_ids": [str(item) for item in payload.get("company_ids", [])],
                "module_access": normalize_module_access(payload.get("module_access", default_module_access(role))),
                "updated_at": utc_now(),
            })
            if "permissions" in payload:
                existing["permissions"] = normalize_permissions(payload.get("permissions"))
            elif "permissions" not in existing:
                existing["permissions"] = default_permissions(role)
            if payload.get("password"):
                existing["password_hash"] = hash_password(str(payload["password"]))
            self._write(data)
            return self.public_user(existing)

    def set_active(self, user_id: str, active: bool) -> dict[str, Any]:
        with self.lock:
            data = self._read()
            user = next((item for item in data.get("users", []) if item.get("id") == user_id), None)
            if not user:
                raise ValueError("User account not found.")
            user["active"] = bool(active)
            user["updated_at"] = utc_now()
            self._write(data)
            return self.public_user(user)

    def delete(self, user_id: str) -> None:
        with self.lock:
            data = self._read()
            users = data.get("users", [])
            if not any(item.get("id") == user_id for item in users):
                raise ValueError("User account not found.")
            data["users"] = [item for item in users if item.get("id") != user_id]
            self._write(data)

    def can_access_company(self, identity: dict[str, Any], company_id: str) -> bool:
        role = (identity.get("roles") or [ROLE_USER])[0]
        if role in {ROLE_MASTER_ADMIN, ROLE_SUPER_ADMIN}:
            return True
        assigned = {str(item) for item in identity.get("company_ids", [])}
        return "*" in assigned or str(company_id) in assigned

    def has_permission(self, identity: dict[str, Any], permission: str) -> bool:
        roles = set(identity.get("roles", []))
        if ROLE_MASTER_ADMIN in roles or ROLE_SUPER_ADMIN in roles:
            return True
        permissions = normalize_permissions(identity.get("permissions"))
        if not permissions:
            permissions = sorted({item for role in roles for item in ROLE_DEFAULT_PERMISSIONS.get(role, set())})
        return permission in permissions

    def has_module_access(self, identity: dict[str, Any], module: str) -> bool:
        """Enforce statutory-module access from persisted JWT grants."""
        roles = set(identity.get("roles", []))
        if ROLE_MASTER_ADMIN in roles:
            return True
        granted = normalize_module_access(identity.get("module_access"))
        if not granted:
            granted = sorted({item for role in roles for item in ROLE_DEFAULT_MODULE_ACCESS.get(role, set())})
        return str(module).strip().upper() in granted


USER_STORE = UserStore()
