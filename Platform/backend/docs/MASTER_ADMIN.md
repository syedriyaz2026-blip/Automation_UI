# Master Admin and statutory module access

## Role hierarchy

The portal has four roles:

- **Master Admin** (`master_admin`) is the highest authority. It can manage
  Super Admin, Admin, and User accounts, assign company scopes, assign
  statutory module grants, manage permissions, and access all reports,
  evidence, intake, and audit data.
- **Super Admin** (`super_admin`) can manage Admin and User accounts and
  operational settings inside the authority granted to it. Its `module_access`
  list is enforced by the backend on every PF and ESI route.
- **Admin** (`admin`) performs company operations and can manage operational
  configuration for its assigned companies.
- **User** (`user`) has restricted access intended for Excel intake and the
  assigned company queue.

## Creating a Super Admin

1. Sign in as Master Admin.
2. Open the **Master Admin** control center from the top bar.
3. Choose **Create account** and select **Super Admin**.
4. Assign one or more company IDs (or `*` for all company scopes).
5. Select the statutory modules the account may open: `PF`, `ESI`, `LWF`,
   and/or `PT`.
6. Select the explicit permissions required for the account and save.

The API rejects attempts by Admins, Users, or Super Admins to create or assign
the Master Admin role or to assign a Super Admin role. A Super Admin with only
`ESI` in `module_access` cannot call PF endpoints, even if its permission list
contains a PF-related permission. This prevents UI-only hiding from becoming a
security boundary.

## Persistence

Portal accounts are stored in:

`backend/config/portal_management.sqlite3`

Each account stores role, active state, company assignments, permissions,
module grants, timestamps, and a PBKDF2 password hash. The legacy
`backend/config/admin_users.json` is only an initial import source for a fresh
database; it is not the live store after SQLite initialization.

The local bootstrap account `A` is promoted to Master Admin on first database
initialization when no Master Admin exists. Its existing password hash is
preserved. No password is written into this document or returned by any API.

## API contract

- `GET /api/v1/auth/me` returns the current role, company assignments,
  permissions, and `module_access`.
- `GET /api/v1/admin/users` lists public account metadata for Master Admin and
  Super Admin.
- `GET /api/v1/admin/permissions` returns the permission and module catalogs
  plus role defaults.
- `POST /api/v1/admin/users` creates an account. A Master Admin may create a
  Super Admin; other roles cannot.
- `PUT /api/v1/admin/users/{id}` updates an account and its grants.
- `POST /api/v1/admin/users/{id}/active` activates or deactivates an account.
- `POST /api/v1/admin/users/{id}/delete` deletes an account, subject to the
  Master Admin protection rules.

All mutating account operations are audit logged. All module-specific routes
perform a server-side module grant check and return `MODULE_ACCESS_DENIED`
when the authenticated account is not granted that module.
