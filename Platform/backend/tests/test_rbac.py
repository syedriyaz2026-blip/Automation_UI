from pathlib import Path

from rbac import (
    PERMISSIONS,
    ROLE_MASTER_ADMIN,
    ROLE_SUPER_ADMIN,
    UserStore,
    default_module_access,
    hash_password,
)


def _seed_store(path: Path) -> UserStore:
    store = UserStore(path)
    store.upsert(
        {
            "username": "master",
            "display_name": "Master Admin",
            "role": ROLE_MASTER_ADMIN,
            "password": "master-password",
            "company_ids": ["*"],
            "permissions": sorted(PERMISSIONS),
            "module_access": ["PF", "ESI", "LWF", "PT"],
        }
    )
    return store


def test_master_admin_can_manage_explicit_super_admin_module_grants(tmp_path):
    store = _seed_store(tmp_path / "portal.sqlite3")
    account = store.upsert(
        {
            "username": "esi-super",
            "display_name": "ESI Super Admin",
            "role": ROLE_SUPER_ADMIN,
            "password": "super-password",
            "company_ids": ["37", "2646"],
            "permissions": ["users.view", "employee_queue.view"],
            "module_access": ["ESI"],
        }
    )

    assert account["role"] == ROLE_SUPER_ADMIN
    assert account["company_ids"] == ["37", "2646"]
    assert account["module_access"] == ["ESI"]
    assert store.has_module_access({"roles": [ROLE_SUPER_ADMIN], "module_access": ["ESI"]}, "ESI")
    assert not store.has_module_access({"roles": [ROLE_SUPER_ADMIN], "module_access": ["ESI"]}, "PF")


def test_master_admin_is_not_limited_by_a_persisted_module_subset(tmp_path):
    store = _seed_store(tmp_path / "portal.sqlite3")
    identity = {"roles": [ROLE_MASTER_ADMIN], "module_access": []}

    assert store.has_module_access(identity, "PF")
    assert store.has_module_access(identity, "ESI")
    assert store.has_module_access(identity, "LWF")
    assert store.has_module_access(identity, "PT")


def test_role_defaults_keep_new_accounts_predictable(tmp_path):
    store = UserStore(tmp_path / "portal.sqlite3")
    account = store.upsert(
        {
            "username": "new-user",
            "display_name": "New User",
            "role": "user",
            "password": "user-password",
            "company_ids": ["37"],
        }
    )

    assert account["module_access"] == default_module_access("user")
    assert account["permissions"]

