import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  ClipboardCheck,
  FileWarning,
  Fingerprint,
  HeartPulse,
  KeyRound,
  LayoutDashboard,
  ListTree,
  LogOut,
  Mail,
  Eye,
  EyeOff,
  Pencil,
  RefreshCw,
  ReceiptText,
  Search,
  Scale,
  ShieldCheck,
  Upload,
  UserRound,
  X,
  XCircle,
} from "lucide-react";
import { api, json } from "./api.js";
import "./intake.css";

const terminal = new Set([
  "COMPLETED",
  "COMPLETED_WITH_ERRORS",
  "FAILED",
  "STOPPED",
  "HANDOVER_REQUIRED",
  "RECOVERY_REQUIRED",
]);
const CONFIGURATION_ACCESS_CODE = "ALPDCORE";
const DEFAULT_DUMMY_CREDENTIALS = {
  username: "",
  password: "",
};
const DEFAULT_INTAKE_SETTINGS = {
  api_enabled: true,
  updated_at: "",
  updated_by: "default",
};

function Status({ children, tone = "neutral" }) {
  return <span className={"status " + tone}>{children}</span>;
}
function recordStatusTone(status) {
  const normalized = String(status || "").toUpperCase();
  if (normalized === "REGISTERED - PENDING APPROVAL" || ["SUCCESS", "COMPLETED", "ESI_COMPLETED", "ESI_REGISTERED"].includes(normalized)) return "green";
  if (["FAILED", "VALIDATION_FAILED", "API_VALIDATION_ERROR", "RECOVERY_REQUIRED"].includes(normalized) || normalized.includes("FAILED")) return "red";
  if (["UPDATE_REVIEW", "DUPLICATE_SKIPPED", "ESI_UPDATE_REVIEW", "ESI_DUPLICATE_SKIPPED"].includes(normalized)) return "amber";
  if (["PROCESSING", "AUTHENTICATING", "AUTO_QUEUED", "RETRY_QUEUED", "ESI_PROCESSING", "ESI_RETRY_QUEUED", "PENDING_AUTOMATION"].includes(normalized)) return "blue";
  return "purple";
}
function recordStatusLabel(status) {
  const labels = {
    "Registered - Pending Approval": "Completed · pending portal approval",
    VALIDATION_FAILED: "Validation failed",
    API_VALIDATION_ERROR: "API validation failed",
    UPDATE_REVIEW: "Update review required",
    DUPLICATE_SKIPPED: "Duplicate skipped",
    ESI_NEW: "Ready for ESI registration",
    ESI_VALIDATION_FAILED: "ESI validation failed",
    ESI_UPDATE_REVIEW: "ESI update review required",
    ESI_RETRY_QUEUED: "ESI retry queued",
    ESI_PENDING_AUTOMATION: "ESI automation pending",
    PENDING_AUTOMATION: "Automation pending",
    RETRY_QUEUED: "Retry queued",
    AUTO_QUEUED: "Queued for processing",
    RECOVERY_REQUIRED: "Recovery required",
  };
  return labels[status] || status || "Pending";
}
function Button({ children, variant = "primary", className = "", ...props }) {
  return (
    <button
      className={"button " + variant + (className ? " " + className : "")}
      {...props}
    >
      {children}
    </button>
  );
}
function message(error) {
  return error && error.message ? error.message : String(error);
}
function formatSourceDate(value) {
  if (!value) return "—";
  const raw = String(value).trim();
  const ddmmyyyy = raw.match(/^(\d{2})-(\d{2})-(\d{4})$/);
  const yyyymmdd = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
  const parsed = ddmmyyyy
    ? new Date(Number(ddmmyyyy[3]), Number(ddmmyyyy[2]) - 1, Number(ddmmyyyy[1]))
    : yyyymmdd
      ? new Date(Number(yyyymmdd[1]), Number(yyyymmdd[2]) - 1, Number(yyyymmdd[3]))
      : new Date(raw);
  if (Number.isNaN(parsed.getTime())) return String(value).replace("T00:00:00", "");
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(parsed);
}

function Login({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      const result = await api(
        "/api/v1/auth/login",
        json({ username, password }),
      );
      onLogin(result);
    } catch (error) {
      setError(message(error));
    }
  }
  return (
    <div className="login-shell">
      <section className="hero">
        <div className="eyebrow">Emanage Statutory Operations Platform</div>
        <h1>Process every employee with clarity and control.</h1>
        <p>
          Manage statutory compliance workflows, validate API records, and keep
          every event, error, and proof file traceable in one local workspace.
        </p>
        <div className="hero-metrics">
          <div>
            <strong>Validation-first</strong>
            <span>Bad records are blocked before submission.</span>
          </div>
          <div>
            <strong>Secure workflows</strong>
            <span>Each statutory process follows its own controls.</span>
          </div>
          <div>
            <strong>Evidence ready</strong>
            <span>Errors and screenshots stay linked.</span>
          </div>
        </div>
      </section>
      <form className="login-card" onSubmit={submit}>
        <div className="icon-badge">
          <ShieldCheck size={21} />
        </div>
        <h2>Welcome back</h2>
        <p>Sign in to the local operations control panel.</p>
        <label>
          <span>
            <UserRound size={15} /> Application user
          </span>
          <input
            id="application-user"
            name="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label>
          <span>
            <KeyRound size={15} /> Application password
          </span>
          <input
            id="application-password"
            name="password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error && (
          <div className="inline-error">
            <AlertTriangle size={16} />
            {error}
          </div>
        )}
        <Button type="submit">
          Sign in <ArrowRight size={17} />
        </Button>
        <small>Local-only control panel · approval is always manual</small>
      </form>
    </div>
  );
}

function ScopedIntakeSettingsPanel({ role, permissions = [] }) {
  const [companies, setCompanies] = useState([]);
  const [deputedCompanies, setDeputedCompanies] = useState([]);
  const [companyId, setCompanyId] = useState("");
  const [deputedCompanyId, setDeputedCompanyId] = useState("");
  const [settings, setSettings] = useState({ ...DEFAULT_INTAKE_SETTINGS });
  const [status, setStatus] = useState("");
  const canView = role === "master_admin" || role === "super_admin" || permissions.includes("intake.settings.view");
  const canModify = role === "master_admin" || role === "super_admin" || permissions.includes("intake.settings.manage");

  useEffect(() => {
    if (!canView) return undefined;
    api("/api/v1/source/companies")
      .then((data) => setCompanies(data.companies || []))
      .catch((error) => setStatus(message(error)));
    return undefined;
  }, [canView]);

  async function chooseCompany(value) {
    setCompanyId(value);
    setDeputedCompanyId("");
    setSettings({ ...DEFAULT_INTAKE_SETTINGS });
    setStatus("");
    if (!value) return setDeputedCompanies([]);
    try {
      const data = await api("/api/v1/source/companies/" + encodeURIComponent(value) + "/deputed");
      setDeputedCompanies(data.deputed_companies || []);
    } catch (error) { setStatus(message(error)); }
  }

  async function chooseDeputed(value) {
    setDeputedCompanyId(value);
    setStatus("");
    if (!companyId || !value) return setSettings({ ...DEFAULT_INTAKE_SETTINGS });
    try {
      setSettings(await api("/api/v1/companies/" + encodeURIComponent(companyId) + "/intake-settings?deputed_company_id=" + encodeURIComponent(value)));
    } catch (error) { setStatus(message(error)); }
  }

  async function save() {
    if (!companyId || !deputedCompanyId || !canModify) return;
    try {
      setStatus("Saving intake settings…");
      const saved = await api("/api/v1/companies/" + encodeURIComponent(companyId) + "/intake-settings", json({ deputed_company_id: deputedCompanyId, api_enabled: settings.api_enabled }));
      setSettings(saved);
      setStatus("Saved for this Company and Deputed Company scope.");
    } catch (error) { setStatus(message(error)); }
  }

  return (
    <section className="admin-panel intake-settings-panel">
      <div className="admin-panel-heading"><div><h2>Data intake controls</h2><p>PF and ESI use API-only intake for every Company + Deputed Company pair.</p></div><Upload size={20} /></div>
      <div className="admin-scope-fields">
        <label>Company<select value={companyId} onChange={(event) => chooseCompany(event.target.value)}><option value="">Select company</option>{companies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label>Deputed Company<select value={deputedCompanyId} onChange={(event) => chooseDeputed(event.target.value)} disabled={!companyId}><option value="">Select deputed company</option>{deputedCompanies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      </div>
      <div className="intake-setting-toggles">
        <button type="button" className={"intake-setting-toggle " + (settings.api_enabled ? "enabled" : "disabled")} disabled={!canModify || !deputedCompanyId} onClick={() => setSettings((current) => ({ ...current, api_enabled: !current.api_enabled }))}><span><strong>API intake</strong><small>Source API data retrieval and API submission</small></span><Status tone={settings.api_enabled ? "green" : "red"}>{settings.api_enabled ? "Enabled" : "Disabled"}</Status></button>
      </div>
      {canModify ? <Button type="button" onClick={save} disabled={!deputedCompanyId}>Save intake settings</Button> : <small className="helper">User accounts can view the scope settings but cannot change them.</small>}
      {status && <small className="helper">{status}</small>}
    </section>
  );
}

function SuperAdminDashboard({ onBack, username, role, permissions = [], moduleAccess = [] }) {
  const isMasterAdmin = role === "master_admin";
  const canManageAccounts = isMasterAdmin || role === "super_admin";
  const canManageEsiAccounts = canManageAccounts && (isMasterAdmin || moduleAccess.includes("ESI")) && permissions.includes("credentials.view");
  const [users, setUsers] = useState([]);
  const [esiAccounts, setEsiAccounts] = useState([]);
  const [report, setReport] = useState(null);
  const [period, setPeriod] = useState("monthly");
  const [availablePermissions, setAvailablePermissions] = useState([]);
  const [roleDefaults, setRoleDefaults] = useState({});
  const [moduleDefaults, setModuleDefaults] = useState({});
  const [editingId, setEditingId] = useState("");
  const [form, setForm] = useState({ username: "", display_name: "", password: "", role: "admin", company_ids: "*", permissions: [], module_access: ["PF", "ESI"] });
  const [esiForm, setEsiForm] = useState({ client_id: "", client_name: "", state: "", district: "", username: "", password: "", active: true });
  const [status, setStatus] = useState("");
  async function refresh() {
    try {
      const requests = [api("/api/v1/admin/reports?period=" + period)];
      if (canManageAccounts) {
        requests.push(api("/api/v1/admin/users"));
        requests.push(api("/api/v1/admin/permissions"));
      }
      if (canManageEsiAccounts) requests.push(api("/api/v1/admin/esi/accounts"));
      const results = await Promise.all(requests);
      const summary = results[0];
      const accounts = results[1];
      const permissionCatalog = results[2];
      const esiCatalog = canManageEsiAccounts ? results[3] : null;
      setUsers(accounts?.users || []);
      setEsiAccounts(esiCatalog?.accounts || []);
      setAvailablePermissions(permissionCatalog?.permissions || []);
      setRoleDefaults(permissionCatalog?.role_defaults || {});
      setModuleDefaults(permissionCatalog?.module_defaults || {});
      setReport(summary);
      setStatus("");
    } catch (error) {
      setStatus(message(error));
    }
  }
  useEffect(() => { refresh(); }, [period, role]);
  async function saveUser(event) {
    event.preventDefault();
    try {
      const endpoint = editingId ? "/api/v1/admin/users/" + encodeURIComponent(editingId) : "/api/v1/admin/users";
      await api(endpoint, json({
        ...form,
        company_ids: form.company_ids.split(",").map((item) => item.trim()).filter(Boolean),
        permissions: form.permissions,
        module_access: form.module_access,
      }));
      setForm({ username: "", display_name: "", password: "", role: "admin", company_ids: "*", permissions: [], module_access: moduleDefaults.admin || ["PF", "ESI"] });
      setEditingId("");
      await refresh();
    } catch (error) { setStatus(message(error)); }
  }
  function beginEdit(account) {
    setEditingId(account.id);
    setForm({
      username: account.username || "",
      display_name: account.display_name || "",
      password: "",
      role: account.role || "user",
      company_ids: (account.company_ids || []).join(",") || "*",
      permissions: account.permissions || roleDefaults[account.role] || [],
      module_access: account.module_access || moduleDefaults[account.role] || [],
    });
    setStatus("");
  }
  function togglePermission(permission) {
    setForm((current) => ({
      ...current,
      permissions: current.permissions.includes(permission)
        ? current.permissions.filter((item) => item !== permission)
        : [...current.permissions, permission],
    }));
  }
  function applyRoleDefaults(nextRole) {
    setForm((current) => ({ ...current, role: nextRole, permissions: roleDefaults[nextRole] || [], module_access: moduleDefaults[nextRole] || [] }));
  }
  function toggleModule(module) {
    setForm((current) => ({
      ...current,
      module_access: current.module_access.includes(module)
        ? current.module_access.filter((item) => item !== module)
        : [...current.module_access, module],
    }));
  }
  async function toggleUser(account) {
    try {
      await api("/api/v1/admin/users/" + encodeURIComponent(account.id) + "/active", json({ active: !account.active }));
      await refresh();
    } catch (error) { setStatus(message(error)); }
  }
  async function deleteUser(account) {
    if (!window.confirm("Delete account " + account.username + "?")) return;
    try {
      await api("/api/v1/admin/users/" + encodeURIComponent(account.id) + "/delete", json({}));
      await refresh();
    } catch (error) { setStatus(message(error)); }
  }
  async function saveEsiAccount(event) {
    event.preventDefault();
    try {
      await api("/api/v1/admin/esi/accounts", json(esiForm));
      setEsiForm({ client_id: "", client_name: "", state: "", district: "", username: "", password: "", active: true });
      setStatus("ESI account metadata saved for this client/state/district mapping. The password is never returned to the UI.");
      await refresh();
    } catch (error) { setStatus(message(error)); }
  }
  async function toggleEsiAccount(account) {
    try {
      await api("/api/v1/admin/esi/accounts/" + encodeURIComponent(account.account_key) + "/active", json({ active: !account.active }));
      await refresh();
    } catch (error) { setStatus(message(error)); }
  }
  return (
    <div className="admin-dashboard">
      <div className="admin-dashboard-heading">
        <div><span className="section-kicker">{isMasterAdmin ? "Master Admin control center" : role === "super_admin" ? "Super Admin control center" : "Admin control center"}</span><h1>{isMasterAdmin ? "Portal management" : role === "super_admin" ? "System administration" : "Company operations"}</h1><p>{isMasterAdmin ? "Create Super Admins, assign statutory modules, manage accounts, and review the complete portal audit." : role === "super_admin" ? "Manage role access, company assignments, operational activity, and time-based reports." : "Manage intake controls, operational activity, and reports for your assigned companies."}</p></div>
        <Button variant="ghost" onClick={onBack}><ChevronLeft size={16} /> Back to modules</Button>
      </div>
      {status && <div className="inline-error"><AlertTriangle size={16} />{status}</div>}
      <div className="admin-stat-grid">
        <div className="admin-stat-card"><span>Accounts</span><strong>{canManageAccounts ? users.length : "—"}</strong><small>{isMasterAdmin ? "Admins and Super Admins" : role === "super_admin" ? "Admins and users" : "Master Admin managed"}</small></div>
        <div className="admin-stat-card"><span>Intake events</span><strong>{report?.totals?.intake_events ?? "—"}</strong><small>{period} window</small></div>
        <div className="admin-stat-card"><span>Accepted rows</span><strong>{report?.totals?.accepted_records ?? "—"}</strong><small>Validated intake</small></div>
        <div className="admin-stat-card"><span>Failed runs</span><strong>{report?.totals?.failed_runs ?? "—"}</strong><small>Audit-ready</small></div>
      </div>
      <div className="admin-dashboard-grid">
        {canManageAccounts && <section className="admin-panel"><div className="admin-panel-heading"><div><h2>{editingId ? "Edit account" : "Create account"}</h2><p>Assign one or more company IDs and explicit permissions. Use * for all company scopes.</p></div><UserRound size={20} /></div>
          <form className="admin-form" onSubmit={saveUser}>
            <label>Username<input value={form.username} required onChange={(event) => setForm({ ...form, username: event.target.value })} /></label>
            <label>Display name<input value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} /></label>
            <label>Password<input type="password" value={form.password} required={!editingId} placeholder={editingId ? "Leave blank to keep current" : "Set a password"} onChange={(event) => setForm({ ...form, password: event.target.value })} /></label>
            <label>Role<select value={form.role} onChange={(event) => applyRoleDefaults(event.target.value)}><option value="admin">Admin</option><option value="user">User</option>{isMasterAdmin && <option value="super_admin">Super Admin</option>}</select></label>
            <label className="wide-field">Company IDs<input value={form.company_ids} onChange={(event) => setForm({ ...form, company_ids: event.target.value })} placeholder="* or 37, 2646" /></label>
            {isMasterAdmin && <fieldset className="permission-fieldset wide-field"><legend>Statutory module access</legend><p className="helper">Choose the platforms this account can open. The backend enforces this grant on every module endpoint.</p><div className="permission-grid">{["PF", "ESI", "LWF", "PT"].map((module) => <label key={module} className="permission-option"><input type="checkbox" checked={form.module_access.includes(module)} onChange={() => toggleModule(module)} />{module}</label>)}</div></fieldset>}
            <fieldset className="permission-fieldset wide-field"><legend>Permissions</legend><div className="permission-grid">{availablePermissions.map((permission) => <label key={permission} className="permission-option"><input type="checkbox" checked={form.permissions.includes(permission)} onChange={() => togglePermission(permission)} />{permission}</label>)}</div></fieldset>
            <div className="admin-form-actions"><Button type="submit">{editingId ? "Save account" : "Create account"} <ArrowRight size={16} /></Button>{editingId && <Button type="button" variant="ghost" onClick={() => { setEditingId(""); setForm({ username: "", display_name: "", password: "", role: "admin", company_ids: "*", permissions: [], module_access: moduleDefaults.admin || ["PF", "ESI"] }); }}>Cancel edit</Button>}</div>
          </form>
        </section>}
        <section className="admin-panel"><div className="admin-panel-heading"><div><h2>Reports &amp; analytics</h2><p>Operational totals based on timestamped intake and run events.</p></div><RefreshCw size={20} /></div>
          <label className="report-period">Reporting period<select value={period} onChange={(event) => setPeriod(event.target.value)}><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option><option value="annual">Annual</option></select></label>
          <div className="report-range">{report?.from ? new Date(report.from).toLocaleString() : "—"} <span>to</span> {report?.to ? new Date(report.to).toLocaleString() : "—"}</div>
          <Button variant="ghost" onClick={refresh}><RefreshCw size={15} /> Refresh report</Button>
        </section>
      </div>
      <ScopedIntakeSettingsPanel role={role} permissions={permissions} />
      {canManageEsiAccounts && <section className="admin-panel esi-account-panel"><div className="admin-panel-heading"><div><h2>ESI account mapping</h2><p>Store the ESIC client, state, and district account independently. Passwords are encrypted locally and are not exposed by the API.</p></div><ShieldCheck size={20} /></div>
        <form className="admin-form" onSubmit={saveEsiAccount}>
          <label>Client ID<input value={esiForm.client_id} required onChange={(event) => setEsiForm({ ...esiForm, client_id: event.target.value })} /></label>
          <label>Client name<input value={esiForm.client_name} onChange={(event) => setEsiForm({ ...esiForm, client_name: event.target.value })} /></label>
          <label>State<input value={esiForm.state} onChange={(event) => setEsiForm({ ...esiForm, state: event.target.value })} /></label>
          <label>District<input value={esiForm.district} onChange={(event) => setEsiForm({ ...esiForm, district: event.target.value })} /></label>
          <label>ESI username<input value={esiForm.username} required onChange={(event) => setEsiForm({ ...esiForm, username: event.target.value })} /></label>
          <label>ESI password<input type="password" value={esiForm.password} required onChange={(event) => setEsiForm({ ...esiForm, password: event.target.value })} /></label>
          <label className="permission-option"><input type="checkbox" checked={esiForm.active} onChange={(event) => setEsiForm({ ...esiForm, active: event.target.checked })} />Active account</label>
          <div className="admin-form-actions"><Button type="submit">Save ESI account <ArrowRight size={16} /></Button></div>
        </form>
        <div className="admin-users-list">{esiAccounts.map((account) => <div className="admin-user-row" key={account.account_key}><div><strong>{account.client_name || account.client_id}</strong><span>Client {account.client_id} · {account.state || "State not set"} · {account.district || "District not set"}</span><small>Updated {account.updated_at}</small></div><Status tone={account.active ? "green" : "red"}>{account.active ? "Active" : "Inactive"}</Status><Button variant="ghost" onClick={() => toggleEsiAccount(account)}>{account.active ? "Deactivate" : "Activate"}</Button></div>)}</div>
      </section>}
      {canManageAccounts && <section className="admin-panel admin-users-panel"><div className="admin-panel-heading"><div><h2>Account access</h2><p>{isMasterAdmin ? "Master Admin controls Super Admin, Admin, and User access, including statutory module grants." : "Super Admin can change Admin and User role, activation, and company assignments."}</p></div><ShieldCheck size={20} /></div>
        <div className="admin-users-list">{users.map((account) => <div className="admin-user-row" key={account.id}><div><strong>{account.display_name}</strong><span>{account.username} · {account.role} · Companies: {(account.company_ids || []).join(", ") || "none"}</span><small>{(account.module_access || []).join(", ") || "No statutory modules"} · {(account.permissions || []).length} permissions</small></div><Status tone={account.active ? "green" : "red"}>{account.active ? "Active" : "Inactive"}</Status><Button variant="ghost" onClick={() => beginEdit(account)}>Edit</Button><Button variant="ghost" onClick={() => toggleUser(account)}>{account.active ? "Deactivate" : "Activate"}</Button>{account.username !== username && <Button variant="ghost" onClick={() => deleteUser(account)}>Delete</Button>}</div>)}</div>
      </section>}
    </div>
  );
}

function StatutoryCards({ activeModule, onSelect, moduleAccess = [], role = "" }) {
  const canOpenModule = (module) => role === "master_admin" || role === "super_admin" || moduleAccess.includes(module);
  const cards = [
    {
      key: "PF",
      label: "PF",
      enabled: canOpenModule("PF"),
      description: "Provident Fund",
       icon: ShieldCheck,
    },
    {
      key: "ESI",
      label: "ESI",
      enabled: canOpenModule("ESI"),
      description: "Employee State Insurance",
      icon: HeartPulse,
    },
    {
      key: "LWF",
      label: "LWF",
      enabled: false,
      description: "Labour Welfare Fund",
      icon: Scale,
    },
    {
      key: "PT",
      label: "PT",
      enabled: false,
      description: "Professional Tax",
      icon: ReceiptText,
    },
  ];
  return (
    <section className="module-section" aria-label="Statutory modules">
      <div className="module-section-heading">
        <div>
          <span className="section-kicker">Choose a workspace</span>
          <h2>Statutory modules</h2>
          <p className="module-intro">
            Select a module to begin a controlled company workflow.
          </p>
        </div>
        <p className="module-status-note">
          <span className="module-status-dot" aria-hidden="true" />
          PF and ESI modules are available now. LWF and PT are under construction.
        </p>
      </div>
      <div className="statutory-cards">
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <button
              key={card.key}
              type="button"
              className={
                "statutory-card module-" + card.key.toLowerCase() + " " +
                (activeModule === card.key ? "active" : "")
              }
              onClick={() => card.enabled && onSelect(card.key)}
              disabled={!card.enabled}
              aria-disabled={!card.enabled}
            >
              <span className="statutory-card-icon">
                <Icon size={19} />
              </span>
              <div>
                <strong>{card.label}</strong>
                <span>{card.description}</span>
                 {!card.enabled && (card.key === "LWF" || card.key === "PT") && <small>Under construction</small>}
                 {!card.enabled && card.key !== "LWF" && card.key !== "PT" && <small>Access not assigned</small>}
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function EsiRecordDetails({ record }) {
  const fields = [
    ["Employee code", "employee_code"],
    ["Employee name", "name"],
    ["Client", "client_name"],
    ["Client ID", "client_id"],
    ["Job location", "job_location"],
    ["ESIC sub-code", "esic_sub_code"],
    ["Date of birth", "dob"],
    ["Date of joining", "doj"],
    ["Gender", "gender"],
    ["Father / husband name", "father_name"],
    ["Marital status", "marital_status"],
    ["Aadhaar number", "aadhaar"],
    ["Mobile", "mobile"],
    ["Alternate mobile", "alternate_mobile"],
    ["Present address", "present_address"],
    ["Present state", "present_state"],
    ["Present district", "present_district"],
    ["Present pincode", "present_pincode"],
    ["Permanent address", "permanent_address"],
    ["Permanent state", "permanent_state"],
    ["Permanent district", "permanent_district"],
    ["Permanent pincode", "permanent_pincode"],
    ["Nominee name", "nominee_name"],
    ["Nominee relation", "nominee_relation"],
    ["Nominee address", "nominee_address"],
    ["Nominee state", "nominee_state"],
    ["Nominee district", "nominee_district"],
    ["Nominee pincode", "nominee_pincode"],
    ["Dispensary", "dispensary_name"],
    ["Dispensary state", "dispensary_state"],
    ["Dispensary district", "dispensary_district"],
    ["Family dispensary", "family_dispensary_name"],
    ["Family dispensary state", "family_dispensary_state"],
    ["Family dispensary district", "family_dispensary_district"],
    ["Bank account", "bank_account"],
    ["Bank account type", "bank_account_type"],
    ["Bank IFSC", "bank_ifsc"],
    ["Bank name", "bank_name"],
    ["Bank branch", "bank_branch"],
    ["PAN", "pan"],
    ["IP already registered", "is_ip_registered"],
    ["Consent", "consent"],
    ["Remarks", "remarks"],
  ];

  return (
    <details className="esi-record-details">
      <summary>View all ESI fields</summary>
      <div className="esi-detail-grid">
        {fields.map(([label, key]) => (
          <div key={key} className="esi-detail-item">
            <span>{label}</span>
            <strong>{record[key] === "" || record[key] == null ? "—" : String(record[key])}</strong>
          </div>
        ))}
      </div>
    </details>
  );
}

function ScopePicker({
  companies,
  deputedCompanies,
  companyId,
  companyName,
  deputedCompanyId,
  deputedCompanyName,
  companyLabel,
  onCompanyChange,
  onDeputedCompanyChange,
  onScopeChange,
  onApply,
  onCancel,
  scopeApplied,
}) {
  const [open, setOpen] = useState(false);
  const [companyQuery, setCompanyQuery] = useState("");
  const [deputedQuery, setDeputedQuery] = useState("");
  const [companyCollapsed, setCompanyCollapsed] = useState(Boolean(companyId));
  const pickerRef = useRef(null);
  const committedScopeRef = useRef(null);

  function beginPicker() {
    committedScopeRef.current = {
      companyId,
      companyName,
      deputedCompanyId,
      deputedCompanyName,
      scopeApplied,
    };
    setCompanyCollapsed(Boolean(companyId));
    setCompanyQuery("");
    setDeputedQuery("");
    setOpen(true);
  }

  function cancelPicker() {
    if (committedScopeRef.current) onCancel?.(committedScopeRef.current);
    setCompanyQuery("");
    setDeputedQuery("");
    setCompanyCollapsed(Boolean(committedScopeRef.current?.companyId));
    setOpen(false);
  }

  useEffect(() => {
    if (!open) return undefined;
    function closePicker(event) {
      if (event.key === "Escape") cancelPicker();
      if (event.type === "pointerdown" && !pickerRef.current?.contains(event.target)) {
        cancelPicker();
      }
    }
    document.addEventListener("keydown", closePicker);
    document.addEventListener("pointerdown", closePicker);
    return () => {
      document.removeEventListener("keydown", closePicker);
      document.removeEventListener("pointerdown", closePicker);
    };
  }, [open]);

  const normalizedCompanyQuery = companyQuery.trim().toLowerCase();
  const normalizedDeputedQuery = deputedQuery.trim().toLowerCase();
  const visibleCompanies = companies.filter((item) =>
    (item.name + " " + (item.code || "") + " " + item.id)
      .toLowerCase()
      .includes(normalizedCompanyQuery),
  );
  // Keep the trigger compact, but make the opened dropdown authoritative:
  // users can scroll the complete source list or narrow it with search.
  const companyResults = visibleCompanies;
  const visibleDeputedCompanies = deputedCompanies.filter((item) =>
    (item.name + " " + item.id).toLowerCase().includes(normalizedDeputedQuery),
  );

  function chooseCompany(id) {
    onCompanyChange(id);
    onScopeChange();
    setCompanyCollapsed(true);
    setCompanyQuery("");
    setDeputedQuery("");
  }

  return (
    <div ref={pickerRef} className={"scope-picker " + (open ? "open" : "")}>
         <button
        type="button"
        className="scope-picker-trigger"
        onClick={() => (open ? cancelPicker() : beginPicker())}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label="Choose company and deputed company scope"
      >
        <span className="scope-picker-part">
          <small>Company</small>
          <strong>{companyName || "Select company"}</strong>
        </span>
        <span className="scope-picker-slash" aria-hidden="true">
          /
        </span>
        <span className="scope-picker-part">
          <small>Deputed Company</small>
          <strong>{deputedCompanyName || "Select deputed company"}</strong>
        </span>
        <ChevronDown
          size={16}
          className={"scope-picker-chevron " + (open ? "open" : "")}
        />
      </button>
      {open && (
        <div
          className={
            "scope-picker-popover " +
            (companyCollapsed && companyId ? "company-collapsed " : "") +
            (!normalizedCompanyQuery && !companyId ? "compact" : "")
          }
          role="dialog"
          aria-label="Company scope selector"
        >
          <div className="scope-picker-heading">
            <div>
              <span className="section-kicker">Processing scope</span>
              <strong>Choose Company and Deputed Company</strong>
            </div>
            <span>{companyId && (deputedCompanyId || deputedCompanies.length === 0) ? "Ready" : "Required"}</span>
          </div>
          <div className="scope-picker-grid">
            <section className="scope-picker-column" aria-label="Company list">
              <div className="scope-picker-label-row">
                <label htmlFor="header-company-search">
                  <span>Company</span>
                </label>
                <div className="scope-picker-label-actions">
                  {companyCollapsed && companyId && (
                    <button
                      type="button"
                      className="scope-change-button"
                      onClick={() => setCompanyCollapsed(false)}
                    >
                      Change
                    </button>
                  )}
                </div>
              </div>
              {companyCollapsed && companyId ? (
                <div className="scope-selected-row">
                  <CheckCircle2 size={16} aria-hidden="true" />
                  <div className="scope-option-copy">
                    <strong>{companyName || "Selected company"}</strong>
                  </div>
                </div>
              ) : (
                <>
                  <div className="scope-search-box">
                    <Search size={15} aria-hidden="true" />
                    <input
                      id="header-company-search"
                      name="header_company_search"
                      value={companyQuery}
                      onChange={(event) => setCompanyQuery(event.target.value)}
                      placeholder="Search company or code"
                      autoComplete="off"
                      autoFocus
                    />
                  </div>
                  <div className="scope-picker-list">
                    {companyResults.length ? (
                      companyResults.map((item) => (
                        <button
                          type="button"
                          key={item.id}
                          className={
                            "scope-picker-option " +
                            (String(item.id) === String(companyId) ? "selected" : "")
                          }
                          onClick={() => chooseCompany(String(item.id))}
                        >
                          <span className="scope-option-copy">
                            <strong>{item.name}</strong>
                          </span>
                          {String(item.id) === String(companyId) && <CheckCircle2 size={15} />}
                        </button>
                      ))
                    ) : (
                      <span className="scope-picker-empty">
                        {normalizedCompanyQuery
                          ? "No companies match this search."
                          : "No company data is available yet."}
                      </span>
                    )}
                  </div>
                </>
              )}
            </section>
            <section className="scope-picker-column" aria-label="Deputed company list">
              <div className="scope-picker-label-row">
                <label htmlFor="header-deputed-search">
                  <span>Deputed Company</span>
                </label>
              </div>
              <div className="scope-search-box">
                <Search size={15} aria-hidden="true" />
                <input
                  id="header-deputed-search"
                  name="header_deputed_search"
                  value={deputedQuery}
                  onChange={(event) => setDeputedQuery(event.target.value)}
                  placeholder={companyId ? "Search deputed company" : "Select a company first"}
                  autoComplete="off"
                  disabled={!companyId}
                />
              </div>
              <div className="scope-picker-list">
                {!companyId ? (
                  <span className="scope-picker-empty">Select a company to load its deputed companies.</span>
                ) : visibleDeputedCompanies.length ? (
                  visibleDeputedCompanies.map((item) => (
                    <button
                      type="button"
                      key={item.id}
                      className={
                        "scope-picker-option " +
                        (String(item.id) === String(deputedCompanyId) ? "selected" : "")
                      }
                      onClick={() => {
                        onDeputedCompanyChange(String(item.id));
                        onScopeChange();
                      }}
                    >
                      <span className="scope-option-copy">
                        <strong>{item.is_all ? "All deputed companies" : item.name}</strong>
                      </span>
                      {String(item.id) === String(deputedCompanyId) && <CheckCircle2 size={15} />}
                    </button>
                  ))
                ) : (
                  <span className="scope-picker-empty">No deputed companies match this search.</span>
                )}
              </div>
            </section>
          </div>
          <div className="scope-picker-footer">
            <span>{deputedCompanies.length ? "Select both companies, then Apply." : "No deputed company is configured; select the Company, then Apply."}</span>
            <div className="scope-picker-footer-actions">
              <button type="button" onClick={cancelPicker}>Cancel</button>
              <Button
                type="button"
                className="scope-apply"
                onClick={() => {
                  onApply();
                  setOpen(false);
                }}
                disabled={!companyId || (deputedCompanies.length > 0 && !deputedCompanyId)}
              >
                Apply
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function App() {
  const [user, setUser] = useState(null),
    [role, setRole] = useState(""),
    [permissions, setPermissions] = useState([]),
    [moduleAccess, setModuleAccess] = useState([]),
    [assignedCompanyIds, setAssignedCompanyIds] = useState([]),
    [companies, setCompanies] = useState([]),
    [companyId, setCompanyId] = useState("");
  const [companyName, setCompanyName] = useState(""),
    [deputedCompanyId, setDeputedCompanyId] = useState(""),
    [deputedCompanyName, setDeputedCompanyName] = useState(""),
    [deputedCompanies, setDeputedCompanies] = useState([]);
  const [records, setRecords] = useState([]),
    [selected, setSelected] = useState([]),
    [validation, setValidation] = useState(null);
  const [run, setRun] = useState(null),
    [errors, setErrors] = useState([]),
    [evidence, setEvidence] = useState([]);
  const [runReport, setRunReport] = useState(null),
    [apiSyncLoading, setApiSyncLoading] = useState(false),
    [apiSyncResult, setApiSyncResult] = useState(null);
  const [credentials, setCredentials] = useState({ ...DEFAULT_DUMMY_CREDENTIALS }),
    [savedCredentials, setSavedCredentials] = useState({ ...DEFAULT_DUMMY_CREDENTIALS }),
    [credentialsEditing, setCredentialsEditing] = useState(false),
    [credentialMessage, setCredentialMessage] = useState(""),
    [credentialConfirmOpen, setCredentialConfirmOpen] = useState(false),
    [credentialHasSavedPassword, setCredentialHasSavedPassword] = useState(false),
    [showEmployerUsername, setShowEmployerUsername] = useState(true),
    [showEmployerPassword, setShowEmployerPassword] = useState(false);
  const [uploadFile, setUploadFile] = useState(null);
  const [uploadErrors, setUploadErrors] = useState([]);
  const [uploadValidated, setUploadValidated] = useState(false);
  const [uploadValidation, setUploadValidation] = useState(null);
  const [uploadValidationLoading, setUploadValidationLoading] = useState(false);
  const [uploadEvents, setUploadEvents] = useState([]);
  const [evidenceFiles, setEvidenceFiles] = useState([]);
  const [notice, setNotice] = useState(""),
    [loading, setLoading] = useState(false),
    [mode] = useState("REAL");
  const [activeModule, setActiveModule] = useState("");
  const [moduleChosen, setModuleChosen] = useState(false);
  const [scopeApplied, setScopeApplied] = useState(false);
  const [intakeSettings, setIntakeSettings] = useState({ ...DEFAULT_INTAKE_SETTINGS });
  const [intakeSettingsSaving, setIntakeSettingsSaving] = useState(false);
  const [navExpanded, setNavExpanded] = useState(
    () => localStorage.getItem("statutory.navExpanded") !== "false",
  );
  const [navGroupOpen, setNavGroupOpen] = useState(
    () => false,
  );
  const [configurationOpen, setConfigurationOpen] = useState(false);
  const [configurationCodeOpen, setConfigurationCodeOpen] = useState(false);
  const [configurationUnlocked, setConfigurationUnlocked] = useState(false);
  const [configurationCode, setConfigurationCode] = useState("");
  const [configurationCodeError, setConfigurationCodeError] = useState("");
  const [drawer, setDrawer] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [queueFilter, setQueueFilter] = useState("all");
  const [timelineRecordId, setTimelineRecordId] = useState("");
  const [adminDashboardOpen, setAdminDashboardOpen] = useState(false);
  const can = (permission) => role === "master_admin" || role === "super_admin" || permissions.includes(permission);
  const canModule = (module) => role === "master_admin" || role === "super_admin" || moduleAccess.includes(module);
  const isEsi = activeModule === "ESI";
  useEffect(() => {
    localStorage.setItem("statutory.navExpanded", String(navExpanded));
  }, [navExpanded]);
  useEffect(() => {
    localStorage.setItem("statutory.navGroupOpen", String(navGroupOpen));
  }, [navGroupOpen]);

  async function loadCompanies() {
    const data = await api("/api/v1/source/companies");
    setCompanies(data.companies || []);
  }
  async function loadDeputedCompanies(id) {
    if (!id) return setDeputedCompanies([]);
    const data = await api(
      "/api/v1/source/companies/" + encodeURIComponent(id) + "/deputed",
    );
    setDeputedCompanies(data.deputed_companies || []);
  }
  async function loadIntakeSettings(id = companyId, deputed = deputedCompanyId) {
    if (!id || !deputed) {
      const defaults = { ...DEFAULT_INTAKE_SETTINGS };
      setIntakeSettings(defaults);
      return defaults;
    }
    const data = await api(
      "/api/v1/companies/" + encodeURIComponent(id) +
        "/intake-settings?deputed_company_id=" + encodeURIComponent(deputed),
    );
    setIntakeSettings(data);
    return data;
  }
  async function saveIntakeSettings() {
    if (!can("intake.settings.manage") || !companyId || !deputedCompanyId) return;
    setIntakeSettingsSaving(true);
    try {
      const saved = await api(
        "/api/v1/companies/" + encodeURIComponent(companyId) + "/intake-settings",
        json({
          deputed_company_id: deputedCompanyId,
          api_enabled: Boolean(intakeSettings.api_enabled),
        }),
      );
      setIntakeSettings(saved);
      setNotice("Data intake settings saved for this Company and Deputed Company scope.");
    } catch (error) {
      setNotice(message(error));
    } finally {
      setIntakeSettingsSaving(false);
    }
  }
  async function loadQueue(id = companyId, activeSettings = intakeSettings) {
    if (!id) return;
    const localData = await api(
      (isEsi ? "/api/v1/esi/inbox?company=" : "/api/v1/inbox?company=") + encodeURIComponent(id) +
        "&deputed_company_id=" + encodeURIComponent(deputedCompanyId),
    );
    const localRecords = (localData.records || []).map((item) => ({
      ...item,
      source_kind:
        item.source === "esi_workbook_upload"
          ? "ESI Excel upload"
          : item.source === "esi_api" || item.source === "esi_source_api"
            ? "ESI API intake"
            : item.source === "workbook_upload"
          ? "Excel upload"
          : item.source === "api"
            ? "API intake"
            : item.source || "Local inbox",
      converted: true,
    }));
    if (isEsi) {
      setRecords(localRecords);
      setQueueFilter("all");
      setSelected([]);
      setValidation(null);
      return;
    }
    const sourceScope = companies.some(
      (item) => String(item.id) === String(id) && item.source,
    );
    // User accounts are Excel-only. Admin and Super Admin can preview the
    // read-only source API, while the explicit sync action persists API rows
    // into the scoped inbox for the automation worker.
    if (can("intake.api") && sourceScope && activeSettings.api_enabled) {
      try {
        const data = await api(
          "/api/v1/source/companies/" +
            encodeURIComponent(id) +
            "/complete-data?deputed_company_id=" +
            encodeURIComponent(deputedCompanyId) +
            "&limit=500",
        );
        const sourceRecords = (data.records || []).map((item, index) => {
          const fields = item.fields || {};
          return {
            record_id: "source-" + (item.employee_id || item.employee_code || index + 1),
            row_number: index + 1,
            emp_code: item.employee_code || "",
            company: companyName,
            deputed_company: deputedCompanyName,
            name: fields.name || "",
            uan: fields.uan || "",
            aadhaar: fields.aadhaar || "",
            dob: fields.dob || "",
            doj: fields.doj || "",
            basic_wage: fields.basic_wage || "",
            status: item.ready_for_pf_intake ? "API_VALIDATED" : "API_VALIDATION_ERROR",
            source_kind: "HRMS / source API",
            converted: item.conversion?.status === "CONVERTED",
            conversion: item.conversion || null,
            validation_errors: item.conversion?.errors || [],
            source_read_only: true,
            missing_required_fields: item.missing_required_fields || [],
            field_sources: item.field_sources || {},
            mandatory_fields: item.mandatory_fields || [],
          };
        });
        const localByKey = new Map(
          localRecords
            .map((item) => [item.uan || item.emp_code || item.record_id, item])
            .filter(([key]) => key),
        );
        const merged = sourceRecords.map((item) => {
          const key = item.uan || item.emp_code || item.record_id;
          const local = localByKey.get(key);
          if (!local) return item;
          localByKey.delete(key);
          return {
            ...local,
            source_kind: `${local.source_kind} + HRMS / source API`,
            conversion: item.conversion,
            converted: item.converted,
          };
        });
        setRecords([...merged, ...localByKey.values()]);
        setQueueFilter("all");
        setSelected([]);
        setValidation(null);
        return;
      } catch (error) {
        setNotice("Source preview unavailable; showing the scoped local inbox. " + message(error));
      }
    }
    setRecords(localRecords);
    setQueueFilter("all");
    setSelected([]);
    setValidation(null);
  }
  async function refreshRun(runId = run && run.run_id) {
    if (!runId) return;
    const next = await api("/api/v1/runs/" + encodeURIComponent(runId));
    setRun(next);
    if (terminal.has(next.status)) {
      const loaded = await Promise.all([
        api("/api/v1/runs/" + encodeURIComponent(runId) + "/errors"),
        api("/api/v1/runs/" + encodeURIComponent(runId) + "/evidence"),
        api("/api/v1/runs/" + encodeURIComponent(runId) + "/report"),
      ]);
      setErrors(loaded[0].errors || []);
      setEvidence(loaded[1].files || []);
      setRunReport(loaded[2].ready ? loaded[2].report : null);
    }
  }
  async function attachAutomaticRun(auto) {
    if (!auto?.run_id) return;
    setRun({
      run_id: auto.run_id,
      status: auto.status || "QUEUED",
      total: auto.total || 0,
      completed: 0,
      results: [],
      events: [],
      message: auto.message || "Automatic processing is queued.",
    });
    setErrors([]);
    setEvidence([]);
    setRunReport(null);
    await refreshRun(auto.run_id);
  }
  useEffect(() => {
    api("/api/v1/auth/me")
      .then((data) => {
        if (!data.authenticated) return;
        setUser(data.username);
        setRole(data.roles?.[0] || "user");
        setPermissions(data.permissions || []);
         setModuleAccess(data.module_access || []);
        setAssignedCompanyIds(data.company_ids || []);
      })
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (user) loadCompanies().catch((error) => setNotice(message(error)));
  }, [user]);
  useEffect(() => {
    if (!scopeApplied || !companyId || (!deputedCompanyId && deputedCompanies.length > 0) || !user) {
      setIntakeSettings({ ...DEFAULT_INTAKE_SETTINGS });
      return;
    }
    (async () => {
      try {
        const settings = await loadIntakeSettings(companyId, deputedCompanyId);
        await loadQueue(companyId, settings);
        if (can("intake.api")) {
          await syncSourceData();
        }
      } catch (error) {
        setNotice(message(error));
      }
    })();
  }, [companyId, deputedCompanyId, deputedCompanies.length, scopeApplied, user, activeModule]);
  useEffect(() => {
    if (role === "user") {
      const firstAllowed = ["PF", "ESI"].find((module) => canModule(module));
      if (firstAllowed) {
        setActiveModule(firstAllowed);
        setModuleChosen(true);
      }
    }
  }, [role, moduleAccess]);
  useEffect(() => {
    setCredentials({ username: "", password: "" });
    setSavedCredentials({ username: "", password: "" });
    setCredentialHasSavedPassword(false);
    setCredentialsEditing(false);
    setCredentialConfirmOpen(false);
    setCredentialMessage(
      companyId && deputedCompanyId
        ? `Credentials for ${companyName || "selected company"} / ${deputedCompanyName || "selected deputed company"}.`
        : "Select a Company and Deputed Company to manage credentials.",
    );
    if (!companyId || !deputedCompanyId) return;
    api(
      "/api/v1/companies/" + encodeURIComponent(companyId) +
        "/credentials/status?deputed_company_id=" + encodeURIComponent(deputedCompanyId),
    )
      .then((data) => {
        if (data.username) {
          const scoped = {
            username: data.username,
            password: data.configured ? "••••••••" : "",
          };
          setCredentials(scoped);
          setSavedCredentials(scoped);
          setCredentialHasSavedPassword(Boolean(data.password_configured || data.configured));
          setCredentialMessage(
            `Saved securely on this local machine for ${companyName || "selected company"} / ${deputedCompanyName || "selected deputed company"}.`,
          );
        }
      })
      .catch(() => {});
  }, [companyId, deputedCompanyId, companyName, deputedCompanyName]);
  useEffect(() => {
    if (!run || terminal.has(run.status)) return undefined;
    const timer = setTimeout(
      () => refreshRun().catch((error) => setNotice(message(error))),
      1000,
    );
    return () => clearTimeout(timer);
  }, [run]);

  async function logout() {
    await api("/api/v1/auth/logout", json({}));
    setUser(null);
    setRole("");
    setPermissions([]);
    setModuleAccess([]);
    setAssignedCompanyIds([]);
    setAdminDashboardOpen(false);
    setRun(null);
    setRunReport(null);
    setApiSyncResult(null);
    setRunReport(null);
    setApiSyncResult(null);
    setApiSyncLoading(false);
    setModuleChosen(false);
    setActiveModule("");
    setConfigurationOpen(false);
    setConfigurationCodeOpen(false);
    setConfigurationUnlocked(false);
    setConfigurationCode("");
    setConfigurationCodeError("");
    setIntakeSettings({ ...DEFAULT_INTAKE_SETTINGS });
    setCredentials({ ...DEFAULT_DUMMY_CREDENTIALS });
    setSavedCredentials({ ...DEFAULT_DUMMY_CREDENTIALS });
    setCredentialsEditing(false);
    setCredentialMessage("");
  }
  function requestConfigurationAccess() {
    if (!can("credentials.view") && !can("intake.settings.view")) {
      setNotice("Configuration is managed by an Admin or Super Admin account.");
      return;
    }
    if (configurationUnlocked) {
      setConfigurationOpen(true);
      return;
    }
    setConfigurationCode("");
    setConfigurationCodeError("");
    setConfigurationCodeOpen(true);
  }
  function unlockConfiguration(event) {
    event.preventDefault();
    if (configurationCode.trim().toUpperCase() !== CONFIGURATION_ACCESS_CODE) {
      setConfigurationCodeError("Invalid configuration code.");
      return;
    }
    setConfigurationUnlocked(true);
    setConfigurationCodeOpen(false);
    setConfigurationOpen(true);
    setConfigurationCode("");
    setConfigurationCodeError("");
  }
  async function validateSelected() {
    if (!selected.length)
      return setNotice("Select at least one employee first.");
    setLoading(true);
    try {
      setValidation(
        await api(
          isEsi ? "/api/v1/esi/validate" : "/api/v1/validate",
            json({ company_id: companyId, deputed_company_id: deputedCompanyId, record_ids: selected }),
        ),
      );
      setNotice("Validation completed. Invalid rows remain blocked.");
    } catch (error) {
      setNotice(message(error));
    } finally {
      setLoading(false);
    }
  }
  async function retryRecord(record, approveUpdate = false) {
    if (!record || !can("runs.start")) return;
    setLoading(true);
    try {
      const result = await api(
        (isEsi ? "/api/v1/esi/companies/" : "/api/v1/companies/") + encodeURIComponent(companyId) +
          "/records/" + encodeURIComponent(record.record_id) + "/retry",
        json({
          deputed_company_id: deputedCompanyId,
          approve_update: approveUpdate,
        }),
      );
      setNotice(result.auto_process?.message || "Record queued for controlled retry.");
      setTimelineRecordId("");
      await loadQueue();
      await attachAutomaticRun(result.auto_process);
    } catch (error) {
      setNotice(message(error));
    } finally {
      setLoading(false);
    }
  }
  async function retryHandoverEmail() {
    if (!run) return;
    try {
      const data = await api(
        "/api/v1/runs/" + encodeURIComponent(run.run_id) + "/handover/email",
        json({}),
      );
      setRun((current) => ({
        ...current,
        handover_email: data.handover_email,
      }));
      setNotice("Handover email was accepted by SMTP.");
    } catch (error) {
      setNotice(message(error));
      refreshRun().catch((refreshError) => setNotice(message(refreshError)));
    }
  }
  async function saveCredentials() {
    try {
      const saved = await api(
        "/api/v1/companies/" + companyId + "/credentials",
        json({ ...credentials, deputed_company_id: deputedCompanyId }),
      );
      setSavedCredentials({ ...credentials });
      setCredentialsEditing(false);
      setCredentialMessage(
        `Saved securely on this local machine for ${companyName || "selected company"} / ${deputedCompanyName || "selected deputed company"}.`,
      );
      setCredentialHasSavedPassword(true);
      setCredentialConfirmOpen(false);
      await attachAutomaticRun(saved.auto_process);
    } catch (error) {
      setCredentialMessage(message(error));
    }
  }
  function requestSaveCredentials() {
    if (!credentials.username.trim() || !credentials.password.trim() || credentials.password === "••••••••") {
      setCredentialMessage("Enter a new username and password before confirming the update.");
      return;
    }
    setCredentialConfirmOpen(true);
  }
  function uploadErrorDetails(error) {
    const rows = error.details?.rows || [];
    const details = rows.flatMap(
      (row) =>
        row.errors?.map(
          (item) =>
            "Row " + row.row + " · " + (item.cell || item.column || "field") + ": " + item.message,
        ) || [],
    );
    return details.length ? details : [message(error)];
  }
  async function validateWorkbook() {
    if (!intakeSettings.excel_enabled) {
      setNotice("Excel upload is disabled for the selected Company and Deputed Company scope.");
      return;
    }
    if (!uploadFile || !companyId || !deputedCompanyId) {
      setNotice("Select both Company and Deputed Company, then choose an .xlsx or .xlsm file.");
      return;
    }
    setUploadValidationLoading(true);
    setUploadValidated(false);
    setUploadValidation(null);
    setUploadErrors([]);
    try {
      const body = new FormData();
      body.append("file", uploadFile);
      body.append("mode", mode);
      body.append("company_id", companyId);
      body.append("deputed_company_id", deputedCompanyId);
      const result = await api(
        (isEsi ? "/api/v1/esi/companies/" : "/api/v1/companies/") + encodeURIComponent(companyId) + "/validate-upload",
        { method: "POST", body },
      );
      setUploadValidation(result);
      setUploadValidated(Boolean(result.validated));
      setNotice(
        "Workbook validated: " +
          (result.valid || 0) +
          " of " +
          (result.total || 0) +
          " rows are ready to upload.",
      );
    } catch (error) {
      const details = uploadErrorDetails(error);
      setUploadValidation(error.details || null);
      setUploadValidated(false);
      setUploadErrors(details);
      setNotice("Upload blocked. Correct the mandatory-field validation errors and validate again.");
    } finally {
      setUploadValidationLoading(false);
    }
  }
  async function uploadWorkbook() {
    if (!intakeSettings.excel_enabled)
      return setNotice("Excel upload is disabled for the selected Company and Deputed Company scope.");
    if (!uploadFile || !companyId || !deputedCompanyId)
      return setNotice(
        "Select both Company and Deputed Company, then choose an .xlsx or .xlsm file.",
      );
    if (!uploadValidated)
      return setNotice("Validate the workbook successfully before uploading it.");
    setLoading(true);
    try {
      const body = new FormData();
      body.append("file", uploadFile);
      body.append("mode", mode);
      body.append("company_id", companyId);
      body.append("deputed_company_id", deputedCompanyId);
      const result = await api(
        (isEsi ? "/api/v1/esi/companies/" : "/api/v1/companies/") + encodeURIComponent(companyId) + "/upload",
        { method: "POST", body },
      );
      setUploadFile(null);
      setUploadErrors([]);
      setUploadValidated(false);
      setUploadValidation(null);
      const auto = result.auto_process;
      setNotice(
        "Upload received: " +
          (result.created || 0) +
          " records added. " +
          (auto?.message || "Automatic processing is queued."),
      );
      await loadQueue();
      await attachAutomaticRun(auto);
    } catch (error) {
      const detail = uploadErrorDetails(error);
      setUploadErrors(detail.length ? detail : [message(error)]);
      setNotice([message(error), ...detail].join(" | "));
    } finally {
      setLoading(false);
    }
  }
  async function syncSourceData() {
    // PF and ESI intake is API-only and automatic for the selected scope.
    if (!companyId) {
      setNotice("Select a Company before fetching API data.");
      return;
    }
    setApiSyncLoading(true);
    setApiSyncResult(null);
    try {
      const result = await api(
        (isEsi ? "/api/v1/esi/companies/" : "/api/v1/companies/") + encodeURIComponent(companyId) + "/sync-source",
        json({
          deputed_company_id: deputedCompanyId || "",
          company_name: companyName,
          deputed_company_name: deputedCompanyName,
          limit: 500,
        }),
      );
      setApiSyncResult(result);
      setNotice(
        "API data converted and validated: " +
          (result.validation?.valid || 0) +
          " of " +
          (result.validation?.total || 0) +
          " rows ready. " +
          (result.auto_process?.message || ""),
      );
      await loadQueue();
      await attachAutomaticRun(result.auto_process);
    } catch (error) {
      setNotice("API intake failed before processing. " + message(error));
    } finally {
      setApiSyncLoading(false);
    }
  }
  async function loadUploadEvents() {
    try {
      const data = await api(
        "/api/v1/uploads/logs?company=" +
          encodeURIComponent(companyId) +
          "&deputed_company_id=" +
          encodeURIComponent(deputedCompanyId),
      );
      setUploadEvents(data.events || []);
    } catch (error) {
      setNotice(message(error));
    }
  }
  async function loadEvidence() {
    try {
      const data = await api(
        "/api/v1/evidence?company=" +
          encodeURIComponent(companyName || "") +
          "&deputed=" +
          encodeURIComponent(deputedCompanyName || ""),
      );
      setEvidenceFiles(data.files || []);
      setDrawer("evidence");
    } catch (error) {
      setNotice(message(error));
    }
  }
  const company = useMemo(
    () => companies.find((item) => item.id === companyId),
    [companies, companyId],
  );
  const companyLabel = (item) =>
    item.name + (item.code ? " · " + item.code : " · ID " + item.id);
  function selectCompany(value) {
    const item = companies.find((companyItem) => companyItem.id === value);
    setCompanyId(value);
    setCompanyName(item?.name || "");
    setDeputedCompanyId("");
    setDeputedCompanyName("");
    setDeputedCompanies([]);
    setScopeApplied(false);
    setRecords([]);
    setSelected([]);
    setValidation(null);
    setRun(null);
    setRunReport(null);
    setApiSyncResult(null);
    setErrors([]);
    setEvidenceFiles([]);
    setUploadEvents([]);
    setUploadErrors([]);
    setNotice("");
    setIntakeSettings({ ...DEFAULT_INTAKE_SETTINGS });
    loadDeputedCompanies(value).catch((error) => setNotice(message(error)));
  }
  function selectDeputedCompany(value) {
    const item = deputedCompanies.find(
      (companyItem) => companyItem.id === value,
    );
    setDeputedCompanyId(value);
    setDeputedCompanyName(item?.name || "");
    setScopeApplied(false);
    setRecords([]);
    setSelected([]);
    setValidation(null);
    setRun(null);
    setRunReport(null);
    setApiSyncResult(null);
    setErrors([]);
    setEvidenceFiles([]);
    setUploadEvents([]);
    setUploadErrors([]);
    setNotice("");
    setIntakeSettings({ ...DEFAULT_INTAKE_SETTINGS });
  }
  function cancelScopeSelection(scope) {
    const restoredCompanyId = scope?.companyId || "";
    setCompanyId(restoredCompanyId);
    setCompanyName(scope?.companyName || "");
    setDeputedCompanyId(scope?.deputedCompanyId || "");
    setDeputedCompanyName(scope?.deputedCompanyName || "");
    setScopeApplied(Boolean(scope?.scopeApplied));
    setDeputedCompanies([]);
    setIntakeSettings({ ...DEFAULT_INTAKE_SETTINGS });
    if (restoredCompanyId) {
      loadDeputedCompanies(restoredCompanyId).catch((error) =>
        setNotice(message(error)),
      );
    }
  }
  function clearModuleScope() {
    setUploadOpen(false);
    setDrawer("");
    setConfigurationOpen(false);
    setModuleChosen(false);
    setActiveModule("");
    setCompanyId("");
    setCompanyName("");
    setDeputedCompanyId("");
    setDeputedCompanyName("");
    setDeputedCompanies([]);
    setScopeApplied(false);
    setRecords([]);
    setSelected([]);
    setValidation(null);
    setUploadErrors([]);
    setRun(null);
    setRunReport(null);
    setApiSyncResult(null);
  }
  const validIds =
    validation && validation.valid_record_ids
      ? validation.valid_record_ids
      : [];
  const authEvent = [...(run && run.events ? run.events : [])]
    .reverse()
    .find((item) => item.state === "CAPTCHA_REQUIRED" && item.screenshot);
  const scopeReady = Boolean(scopeApplied && companyId && (deputedCompanyId || deputedCompanies.length === 0));
  const queueRecords = records.filter((item) => {
    if (queueFilter === "pending") {
      return ["NEW", "AUTO_QUEUED", "Queued", "API_VALIDATED", "PROCESSING", "AUTHENTICATING", "RETRY_QUEUED", "ESI_NEW", "ESI_PROCESSING", "ESI_RETRY_QUEUED", "PENDING_AUTOMATION"].includes(item.status);
    }
    if (queueFilter === "completed") {
      return ["SUCCESS", "COMPLETED", "COMPLETED_WITH_ERRORS", "Registered - Pending Approval", "ESI_COMPLETED", "ESI_REGISTERED"].includes(item.status);
    }
    if (queueFilter === "failed") {
      return item.status === "FAILED" || item.status === "VALIDATION_FAILED" || item.status === "API_VALIDATION_ERROR" || item.status === "ESI_VALIDATION_FAILED" || String(item.status).toLowerCase().includes("failed");
    }
    if (queueFilter === "attention") {
      return ["UPDATE_REVIEW", "DUPLICATE_SKIPPED", "RECOVERY_REQUIRED", "ESI_UPDATE_REVIEW", "ESI_DUPLICATE_SKIPPED"].includes(item.status) || item.pending_update || (item.duplicate_count || 0) > 0;
    }
    return true;
  });
  const queueFilterTitle = {
    all: "Incoming records",
    pending: "Pending workflows",
    completed: "Completed workflows",
    failed: "Failed workflows",
    attention: "Needs attention",
  }[queueFilter];
  const timelineRecord = records.find((item) => item.record_id === timelineRecordId);
  const reportSummary = runReport?.summary || {};
  const reportBaseUrl = runReport?.run_id
    ? "/api/v1/runs/" + encodeURIComponent(runReport.run_id) + "/report/"
    : "";
  if (!user) return <Login onLogin={(result) => { setUser(result.username); setRole(result.role || result.roles?.[0] || "user"); setPermissions(result.permissions || []); setModuleAccess(result.module_access || []); setAssignedCompanyIds(result.company_ids || []); }} />;
  if (adminDashboardOpen && (can("users.view") || can("reports.view"))) return <>
    <header className="topbar admin-topbar"><div className="brand"><div className="brand-mark" aria-label="Emanage Statutory Operations logo"><ShieldCheck size={19} /></div><div>Emanage Statutory Operations<small>{role === "master_admin" ? "Master Admin control center" : role === "super_admin" ? "Super Admin control center" : "Admin control center"}</small></div></div><div className="top-actions"><span className="user-pill"><UserRound size={14} /> {user}</span><button className="logout-button" onClick={logout}><LogOut size={16} /> Logout</button></div></header>
    <SuperAdminDashboard username={user} role={role} permissions={permissions} moduleAccess={moduleAccess} onBack={() => setAdminDashboardOpen(false)} />
  </>;
  if (!moduleChosen)
    return (
      <div className="module-screen">
        <header className="topbar module-topbar">
          <div className="brand">
            <div className="brand-mark" aria-label="Emanage Statutory Operations logo">
              <ShieldCheck size={19} />
            </div>
            <div>
              Emanage Statutory Operations<small>Local compliance workspace</small>
            </div>
          </div>
          <div className="top-actions">
             {(can("users.view") || can("reports.view")) && <Button variant="ghost" onClick={() => setAdminDashboardOpen(true)}><ShieldCheck size={15} /> {role === "master_admin" ? "Master Admin" : role === "super_admin" ? "Super Admin" : "Admin Panel"}</Button>}
            <span className="user-pill">
              <UserRound size={14} /> {user}
            </span>
            <button className="logout-button" onClick={logout} title="Sign out">
              <LogOut size={16} /> <span>Logout</span>
            </button>
          </div>
        </header>
        <main className="module-only">
          <StatutoryCards
            activeModule={activeModule}
             role={role}
             moduleAccess={moduleAccess}
            onSelect={(module) => {
               if (!canModule(module)) return;
              setActiveModule(module);
              setModuleChosen(true);
            }}
          />
        </main>
      </div>
    );

  const intakePanel = (
    <div className="panel intake-panel">
      <div className="panel-heading">
        <div>
          <h2>Bring in employee data</h2>
          <p>
            {isEsi
              ? "ESI employee records arrive through the API, then are normalized and validated before entering the scoped queue."
              : "PF employee records arrive through the API, are validated, and are queued automatically. Only employees whose DOJ falls within the latest 48 hours are included."}
          </p>
        </div>
        <Upload size={19} className="panel-icon" />
      </div>
      <div className="intake-source-grid">
          {can("intake.api") && <div className={"intake-source-card api-intake-card " + (!intakeSettings.api_enabled ? "intake-disabled" : "")}>
           <div className="intake-source-heading">
             <div>
               <strong>API intake <Status tone={intakeSettings.api_enabled ? "green" : "red"}>{intakeSettings.api_enabled ? "Enabled" : "Disabled"}</Status></strong>
               <span>Fetch, convert, validate, and queue the latest source records for this scope.</span>
             </div>
             <Button
               variant="ghost"
               onClick={syncSourceData}
               disabled={!intakeSettings.api_enabled || apiSyncLoading || !companyId || !deputedCompanyId}
             >
               {apiSyncLoading ? "Fetching…" : "Fetch latest API data"}
             </Button>
           </div>
           <code>
             {isEsi ? "/api/v1/esi/companies/" : "/api/v1/companies/"}{companyId}/sync-source?deputed_company_id={deputedCompanyId}
           </code>
           <small>{intakeSettings.api_enabled ? "Only employees joined in the latest 48 hours are included. Mandatory fields are converted to DD-MM-YYYY and validated before automation." : "API intake is disabled for this Company and Deputed Company scope. An Admin or Super Admin can enable it in Configuration."}</small>
           {apiSyncResult && (
             <div className="api-sync-result" role="status">
               <CheckCircle2 size={15} />
               <span>
                 <strong>Last API sync: {apiSyncResult.validation?.valid || 0} ready</strong>
                 <small>{apiSyncResult.validation?.invalid || 0} rejected · {apiSyncResult.ingest?.created || 0} new records stored · {apiSyncResult.queried_at || "timestamp unavailable"}</small>
               </span>
             </div>
           )}
          </div>}
      </div>
      <div className="intake-footer">
        <span>
          Accepted records are automatically validated and queued for
          processing.
        </span>
        {isEsi && companyId && deputedCompanyId && can("reports.download") && <span className="intake-report-links"><a className="template-link" href={`/api/v1/esi/report?company=${encodeURIComponent(companyId)}&deputed_company_id=${encodeURIComponent(deputedCompanyId)}&company_name=${encodeURIComponent(companyName)}&deputed_company_name=${encodeURIComponent(deputedCompanyName)}&format=xlsx`} download>Download ESI report</a><a className="template-link" href={`/api/v1/esi/report?company=${encodeURIComponent(companyId)}&deputed_company_id=${encodeURIComponent(deputedCompanyId)}&format=json`} download>Audit JSON</a></span>}
      </div>
      {uploadErrors.length > 0 && (
        <div className="upload-validation-errors" role="alert">
          <div className="upload-validation-heading">
            <FileWarning size={16} />
            <strong>Upload blocked — correct these validation errors</strong>
          </div>
          <ul>
            {uploadErrors.map((item, index) => (
              <li key={item + index}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {uploadEvents.length > 0 && (
        <div className="upload-log">
          {uploadEvents.map((event, index) => (
            <div className="upload-log-row" key={event.timestamp + index}>
              <Status tone={event.status === "ACCEPTED" ? "green" : "red"}>
                {event.status}
              </Status>
              <span>{event.filename}</span>
              <span>
                {event.valid}/{event.total} valid
              </span>
              <small>{event.timestamp}</small>
            </div>
          ))}
        </div>
      )}
      {evidenceFiles.length > 0 && (
        <div className="upload-log evidence-log">
          <strong>Evidence files</strong>
          {evidenceFiles.map((file) => (
            <div className="upload-log-row" key={file.path}>
              <span>{file.stage}</span>
              <span>{file.name}</span>
              <small>{file.modified}</small>
            </div>
          ))}
        </div>
      )}
    </div>
  );
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            <ShieldCheck size={19} />
          </div>
          <div>
            Emanage Statutory Operations<small>Local compliance workspace</small>
          </div>
        </div>
        <ScopePicker
          companies={companies}
          deputedCompanies={deputedCompanies}
          companyId={companyId}
          companyName={companyName}
          deputedCompanyId={deputedCompanyId}
          deputedCompanyName={deputedCompanyName}
          companyLabel={companyLabel}
          onCompanyChange={selectCompany}
          onDeputedCompanyChange={selectDeputedCompany}
          onScopeChange={() => setScopeApplied(false)}
          onApply={() => {
            setScopeApplied(true);
            setDrawer("");
            setConfigurationOpen(false);
            setUploadOpen(false);
          }}
          onCancel={cancelScopeSelection}
          scopeApplied={scopeApplied}
        />
        <div className="top-actions">
          {moduleChosen && (
            <Button
              variant="ghost"
              className="top-back-modules"
              onClick={clearModuleScope}
              title="Back to modules"
            >
              <ChevronLeft size={15} /> <span>Back to modules</span>
            </Button>
          )}
          <span className="user-pill">
            <UserRound size={14} /> {user}
          </span>
          <button className="logout-button" onClick={logout} title="Sign out">
            <LogOut size={16} /> <span>Logout</span>
          </button>
        </div>
      </header>
      {configurationCodeOpen && !configurationOpen && !drawer && (
        <div
          className="config-access-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Configuration access"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              setConfigurationCodeOpen(false);
              setConfigurationCodeError("");
            }
          }}
        >
          <form className="config-access-card" onSubmit={unlockConfiguration}>
            <div className="config-access-icon">
              <KeyRound size={20} />
            </div>
            <span className="section-kicker">Protected settings</span>
            <h2>Configuration access</h2>
            <p>Enter the access code to manage company credentials.</p>
            <label>
              <span>Access code</span>
              <input
                type="password"
                value={configurationCode}
                onChange={(event) => {
                  setConfigurationCode(event.target.value);
                  setConfigurationCodeError("");
                }}
                placeholder="Enter access code"
                autoComplete="off"
                autoFocus
              />
            </label>
            {configurationCodeError && (
              <div className="config-access-error" role="alert">
                {configurationCodeError}
              </div>
            )}
            <div className="config-access-actions">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setConfigurationCodeOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit">Continue</Button>
            </div>
          </form>
        </div>
      )}
      {(configurationOpen || drawer) && (
        <div
          className="config-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={
            configurationOpen
              ? "Configuration"
              : drawer === "evidence"
                ? "Evidence"
                : drawer === "live"
                  ? "Live process"
                  : "Error handover and proof"
          }
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              setConfigurationOpen(false);
              setDrawer("");
            }
          }}
        >
          <div className="config-drawer">
            {configurationOpen && (
              <>
                <div className="config-drawer-heading">
                  <div>
                    <span className="section-kicker">Workspace settings</span>
                    <h2>Configuration</h2>
                    <p>
                      Credentials are encrypted and saved locally for each Company and Deputed Company scope.
                    </p>
                  </div>
                  <div className="config-heading-actions">
                    <button
                      type="button"
                      className="icon-button"
                      onClick={() => setConfigurationOpen(false)}
                      aria-label="Close configuration"
                    >
                      <X size={20} />
                    </button>
                  </div>
                </div>
                <div className="config-scope-card" aria-label="Selected credential scope">
                  <div>
                    <span>Company</span>
                    <strong>{companyName || "No company selected"}</strong>
                  </div>
                  <div>
                    <span>Deputed Company</span>
                    <strong>{deputedCompanyName || "No deputed company selected"}</strong>
                  </div>
                </div>
                <div className="form-grid">
                  <label>
                    <span>Employer username</span>
                    <div className="credential-input-wrap">
                      <input
                        id="employer-username"
                        name="employer_username"
                        type={showEmployerUsername ? "text" : "password"}
                        value={credentials.username}
                        onChange={(event) =>
                          setCredentials({
                            ...credentials,
                            username: event.target.value,
                          })
                        }
                        autoComplete="off"
                        disabled={!credentialsEditing}
                      />
                      <button
                        type="button"
                        className="credential-eye"
                        onClick={() => setShowEmployerUsername((value) => !value)}
                        aria-label={showEmployerUsername ? "Hide employer username" : "Show employer username"}
                        title={showEmployerUsername ? "Hide username" : "Show username"}
                      >
                        {showEmployerUsername ? <EyeOff size={17} /> : <Eye size={17} />}
                      </button>
                    </div>
                  </label>
                  <label>
                    <span>Employer password</span>
                    <div className="credential-input-wrap">
                      <input
                        id="employer-password"
                        name="employer_password"
                        type={showEmployerPassword ? "text" : "password"}
                        value={credentials.password}
                        onChange={(event) =>
                          setCredentials({
                            ...credentials,
                            password: event.target.value,
                          })
                        }
                        autoComplete="off"
                        disabled={!credentialsEditing}
                      />
                      <button
                        type="button"
                        className="credential-eye"
                        onClick={() => setShowEmployerPassword((value) => !value)}
                        aria-label={showEmployerPassword ? "Hide employer password" : "Show employer password"}
                        title={showEmployerPassword ? "Hide password" : "Show password"}
                      >
                        {showEmployerPassword ? <EyeOff size={17} /> : <Eye size={17} />}
                      </button>
                    </div>
                  </label>
                </div>
                {can("credentials.manage") && <div className="credential-edit-row">
                  {credentialsEditing ? (
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() => {
                        setCredentials({ ...savedCredentials });
                        setCredentialsEditing(false);
                        setCredentialMessage("");
                      }}
                    >
                      Cancel edit
                    </Button>
                  ) : (
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() => {
                        setCredentialsEditing(true);
                        setCredentials((current) => ({ ...current, password: "" }));
                        setCredentialMessage("");
                      }}
                    >
                      <Pencil size={16} /> Modify credentials
                    </Button>
                  )}
                </div>}
                {can("credentials.manage") && <Button
                  variant="ghost"
                  onClick={requestSaveCredentials}
                  disabled={!credentialsEditing}
                >
                  Save credentials
                </Button>}
                {can("credentials.manage") && credentialConfirmOpen && (
                  <div className="credential-confirm" role="alertdialog" aria-modal="true" aria-label="Confirm credential update">
                    <strong>Confirm credential update</strong>
                    <p>Update credentials for the selected Company and Deputed Company?</p>
                    <div>
                      <Button type="button" variant="ghost" onClick={() => setCredentialConfirmOpen(false)}>Cancel</Button>
                      <Button type="button" onClick={saveCredentials}>Confirm update</Button>
                    </div>
                  </div>
                )}
                <small className="helper">
                  {credentialMessage ||
                    (!can("credentials.view")
                      ? "Credential settings are managed by an Admin or Super Admin."
                      : credentialHasSavedPassword
                      ? "Saved credentials are protected locally. Click Modify credentials to replace them."
                      : "Click Modify credentials to add credentials for this scope.")}
                </small>
              </>
            )}
            {drawer && (
              <>
                <div className="config-drawer-heading">
                  <div>
                    <span className="section-kicker">
                      {drawer === "evidence"
                        ? "Audit trail"
                        : "Operations review"}
                    </span>
                    <h2>
                      {drawer === "evidence"
                        ? "Evidence"
                        : drawer === "live"
                          ? "Live process"
                          : "Error handover & proof"}
                    </h2>
                    <p>
                      {drawer === "evidence"
                        ? "Screenshots captured through login and later workflow stages."
                        : drawer === "live"
                          ? "Every state transition is recorded for operations review."
                          : "Failed rows, reasons, and evidence stay linked."}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="icon-button"
                    onClick={() => setDrawer("")}
                    aria-label="Close panel"
                  >
                    <X size={20} />
                  </button>
                </div>
                {drawer === "evidence" ? (
                  <>
                    <div className="evidence-scope" aria-label="Evidence scope">
                      <div>
                        <span>Selected company</span>
                        <strong>{companyName || "Not selected"}</strong>
                      </div>
                      <div>
                        <span>Selected deputed company</span>
                        <strong>{deputedCompanyName || "Not selected"}</strong>
                      </div>
                      <small>
                        {evidenceFiles.length} evidence file{evidenceFiles.length === 1 ? "" : "s"} found for this scope.
                      </small>
                    </div>
                    <div className="evidence-browser">
                    {evidenceFiles.length ? (
                      evidenceFiles.map((file) => (
                        <a
                          className="evidence-item"
                          href={file.url}
                          target="_blank"
                          rel="noreferrer"
                          key={file.url}
                        >
                          <img src={file.url} alt={file.stage + " evidence"} />
                          <div>
                            <Status
                              tone={
                                file.stage === "Success Evidence"
                                  ? "green"
                                  : file.stage === "Failure Evidence"
                                    ? "red"
                                    : "purple"
                              }
                            >
                              {file.stage || "Evidence"}
                            </Status>
                            <strong>{file.name}</strong>
                            <small>
                              {file.company} · {file.deputed_company}
                            </small>
                            <small>
                              {new Date(file.modified).toLocaleString()}
                            </small>
                          </div>
                        </a>
                      ))
                    ) : (
                      <div className="empty compact">
                        No evidence found for the selected company scope.
                      </div>
                    )}
                    </div>
                  </>
                ) : drawer === "live" ? (
                  <>
                    <div className="run-banner">
                      <Status
                        tone={
                          run &&
                          (run.status === "HANDOVER_REQUIRED" ||
                            run.status === "FAILED")
                            ? "red"
                            : "purple"
                        }
                      >
                        {run ? run.status : "READY"}
                      </Status>
                      <strong>
                        {run
                          ? run.current_employee || "Preparing run"
                          : "No run started"}
                      </strong>
                      <span>
                        {run
                          ? (run.completed || 0) +
                            "/" +
                            (run.total || 0) +
                            " completed"
                          : "Select validated employees to begin"}
                      </span>
                    </div>
                    <pre className="event-log">
                      {run && run.events
                        ? run.events
                            .map(
                              (item) =>
                                item.timestamp +
                                "  " +
                                item.event +
                                "  " +
                                (item.message || ""),
                            )
                            .join("\n")
                        : "Run events will appear here."}
                    </pre>
                  </>
                ) : errors.length ? (
                  errors.map((error) => (
                    <div
                      className="error-row"
                      key={String(error.row) + error.message}
                    >
                      <div>
                        <Status tone="red">{error.category}</Status>
                        <strong>
                          Row {error.row} · {error.employee_name}
                        </strong>
                        <small>{error.workflow_step}</small>
                      </div>
                      <p>{error.message}</p>
                    </div>
                  ))
                ) : (
                  <div className="empty compact">
                    No errors recorded for this run.
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
      <div
        className={
          "workspace " +
          (scopeReady ? "nav-open " : "nav-hidden ") +
          (navExpanded ? "nav-expanded" : "nav-collapsed")
        }
      >
        {scopeReady && <aside className="sidebar">
          <div className="sidebar-header">
             <span className="nav-text">Workspace</span>
            <button
              type="button"
              className="sidebar-toggle"
              onClick={() => setNavExpanded((value) => !value)}
              aria-label={
                navExpanded ? "Collapse navigation" : "Expand navigation"
              }
              aria-expanded={navExpanded}
            >
              {navExpanded ? (
                <ChevronLeft size={18} />
              ) : (
                <ChevronRight size={18} />
              )}
            </button>
          </div>
          <button
            type="button"
             className={
               "nav-item nav-button " +
               (!uploadOpen && !drawer && !configurationOpen ? "active" : "")
             }
             onClick={() => {
               setUploadOpen(false);
               setDrawer("");
             }}
            title="Employee queue"
          >
            <LayoutDashboard size={18} />
            <span className="nav-text">Employee queue</span>
          </button>
          {can("monitoring.view") && <button
            type="button"
            className="nav-group-toggle"
            onClick={() => setNavGroupOpen((value) => !value)}
            aria-expanded={navGroupOpen}
          >
            <span>
              <ListTree size={18} />
              <span className="nav-text">Monitoring</span>
            </span>
            <ChevronDown
              className={"chevron " + (navGroupOpen ? "open" : "")}
              size={16}
            />
          </button>}
          {can("monitoring.view") && navGroupOpen && (
            <div className="nav-submenu">
              <button
                type="button"
                className={"nav-item nav-button " + (queueFilter === "pending" ? "active" : "")}
                onClick={() => { setQueueFilter("pending"); setUploadOpen(false); setDrawer(""); }}
                title="Pending workflows"
              >
                <RefreshCw size={18} />
                <span className="nav-text">Pending workflows</span>
              </button>
              <button
                type="button"
                className={"nav-item nav-button " + (queueFilter === "completed" ? "active" : "")}
                onClick={() => { setQueueFilter("completed"); setUploadOpen(false); setDrawer(""); }}
                title="Completed workflows"
              >
                <CheckCircle2 size={18} />
                <span className="nav-text">Completed workflows</span>
              </button>
              <button
                type="button"
                className={"nav-item nav-button " + (queueFilter === "failed" ? "active" : "")}
                onClick={() => { setQueueFilter("failed"); setUploadOpen(false); setDrawer(""); }}
                title="Failed workflows"
              >
                <AlertTriangle size={18} />
                <span className="nav-text">Failed workflows</span>
              </button>
              <button
                type="button"
                className={"nav-item nav-button " + (queueFilter === "attention" ? "active" : "")}
                onClick={() => { setQueueFilter("attention"); setUploadOpen(false); setDrawer(""); }}
                title="Duplicate and update review"
              >
                <AlertTriangle size={18} />
                <span className="nav-text">Needs attention</span>
              </button>
              <button
                type="button"
                className={
                  "nav-item nav-button " + (drawer === "live" ? "active" : "")
                }
                onClick={() => setDrawer("live")}
                title="Live process"
              >
                <Activity size={18} />
                <span className="nav-text">Live process</span>
              </button>
              <button
                type="button"
                className={
                  "nav-item nav-button " + (drawer === "errors" ? "active" : "")
                }
                onClick={() => setDrawer("errors")}
                title="Error handover and proof"
              >
                <ClipboardCheck size={18} />
                <span className="nav-text">Error handover &amp; proof</span>
              </button>
              {can("evidence.view") && <button
                type="button"
                className="nav-item nav-button"
                onClick={loadEvidence}
                title="Evidence"
              >
                <Fingerprint size={18} />
                <span className="nav-text">Evidence</span>
              </button>}
            </div>
          )}
          {can("credentials.view") && <button
            type="button"
            className={
              "nav-item nav-button " +
              (configurationOpen || configurationCodeOpen ? "active" : "")
            }
            onClick={requestConfigurationAccess}
            title="Configuration"
          >
            <KeyRound size={18} />
            <span className="nav-text">Configuration</span>
          </button>}
          <div className="sidebar-spacer" />
        </aside>}
        <main
          className={
            "content " +
            (!scopeReady ? "scope-locked" : "")
          }
        >
          {false ? (
            <div className="page-view">
              <div className="page-view-heading page-view-heading-with-scope">
                <div>
                  <h1>API intake</h1>
                   <p>Bring in employee data for the selected company and deputed company scope.</p>
                </div>
                <div className="scope-context">
                  <div><span>Company</span><strong>{companyName || "Select company first"}</strong></div>
                  <div><span>Deputed Company</span><strong>{deputedCompanyName || "Select deputed company first"}</strong></div>
                </div>
              </div>
              {deputedCompanyId ? intakePanel : (
                <div className="scope-empty panel">
                  <strong>Select both scope fields first</strong>
                  <span>Upload and API intake are locked until Company and Deputed Company are selected.</span>
                </div>
              )}
            </div>
          ) : (
            <>
          <div className="page-heading-row">
            <div className="page-heading">
              <div className="scope-intro">
                {scopeReady && (
                  <div className="step-visual" aria-hidden="true">
                    <span className="step-icon"><ListTree size={17} /></span>
                  </div>
                )}
                <div>
                  <h1>
                    {deputedCompanyId
                      ? activeModule + " workspace"
                      : "Start a statutory workflow"}
                  </h1>
                  <p>
                    {deputedCompanyId
                      ? "Review, validate, and explicitly start a supervised statutory run."
                        : "Use the scope selector in the header to choose Company and Deputed Company."}
                  </p>
                </div>
              </div>
            </div>
            {deputedCompanyId && (
              <div className="metrics">
                <div className="metric-card">
                  <div className="metric-label">
                    <span>New records</span>
                    <Activity size={16} />
                  </div>
                  <strong>{records.filter((item) => ["NEW", "ESI_NEW"].includes(item.status)).length}</strong>
                  <small>Waiting for automatic validation</small>
                </div>
                <div className="metric-card">
                  <div className="metric-label">
                    <span>In progress</span>
                    <RefreshCw size={16} />
                  </div>
                  <strong>
                    {records.filter((item) => ["AUTO_QUEUED", "RETRY_QUEUED", "PROCESSING", "AUTHENTICATING", "ESI_PROCESSING", "ESI_RETRY_QUEUED", "PENDING_AUTOMATION"].includes(item.status)).length}
                  </strong>
                  <small>Queued or currently processing</small>
                </div>
                <div className="metric-card">
                  <div className="metric-label">
                    <span>Completed</span>
                    <CheckCircle2 size={16} />
                  </div>
                  <strong>{records.filter((item) => ["SUCCESS", "COMPLETED", "COMPLETED_WITH_ERRORS", "Registered - Pending Approval", "ESI_COMPLETED", "ESI_REGISTERED"].includes(item.status)).length}</strong>
                  <small>Successfully processed records</small>
                </div>
                <div className="metric-card">
                  <div className="metric-label">
                    <span>Failed</span>
                    <AlertTriangle size={16} />
                  </div>
                  <strong>{records.filter((item) => ["FAILED", "VALIDATION_FAILED", "API_VALIDATION_ERROR", "RECOVERY_REQUIRED", "ESI_VALIDATION_FAILED"].includes(item.status)).length}</strong>
                  <small>Records needing retry or correction</small>
                </div>
                <div className="metric-card">
                  <div className="metric-label">
                    <span>Run status</span>
                    <Activity size={16} />
                  </div>
                  <strong className="metric-status">{run ? run.status : "Ready"}</strong>
                  <small>{run ? (run.completed || 0) + "/" + (run.total || 0) + " rows processed" : "Automatic processing is idle"}</small>
                </div>
              </div>
            )}
          </div>
          {runReport && (
            <section className="panel run-report-panel" aria-label="Processing report">
              <div className="panel-heading">
                <div>
                  <span className="section-kicker">Completed workflow</span>
                  <h2>Processing report ready</h2>
                  <p>
                    Generated {runReport.generated_at ? new Date(runReport.generated_at).toLocaleString() : "—"} · scoped to {companyName || "the selected company"} / {deputedCompanyName || "the selected deputed company"}
                  </p>
                </div>
                <CheckCircle2 size={19} className="panel-icon" />
              </div>
              <div className="run-report-summary">
                <div><span>Total</span><strong>{reportSummary.total ?? run?.total ?? 0}</strong></div>
                <div><span>Successful</span><strong className="report-success">{reportSummary.success_count ?? 0}</strong></div>
                <div><span>Failed</span><strong className="report-failed">{reportSummary.failed_count ?? 0}</strong></div>
                <Status tone={run?.status === "FAILED" ? "red" : "green"}>{run?.status || "Completed"}</Status>
              </div>
              <div className="run-report-actions">
                <a className="button" href={reportBaseUrl + "processing_report.xlsx"} download>Download Excel report</a>
                <a className="button ghost" href={reportBaseUrl + "processing_report.json"} download>Download JSON report</a>
                <Button type="button" variant="ghost" onClick={loadEvidence}><Fingerprint size={15} /> View evidence</Button>
              </div>
            </section>
          )}
          {notice && (
            <div className="notice">
              <CircleHelp size={16} />
              {notice}
              <button onClick={() => setNotice("")}>×</button>
            </div>
          )}
          {!deputedCompanyId && (
            <div className="scope-empty panel">
              <strong>Select both scope fields to continue</strong>
              <span>
                Choose the Company and Deputed Company above to access {activeModule || "statutory"} operations.
              </span>
            </div>
          )}
          <div className="columns">
            <section>
              <div className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>{queueFilterTitle}</h2>
                    <p>
                      Records are validated and processed automatically after
                      acceptance.
                    </p>
                  </div>
                  <Button variant="ghost" onClick={syncSourceData} disabled={apiSyncLoading || !companyId || !deputedCompanyId}>
                    <RefreshCw size={16} /> Refresh
                  </Button>
                </div>
                <div className="toolbar">
                  <Button
                    variant="ghost"
                    onClick={() =>
                      setSelected(
                      queueRecords
                          .filter((item) => !item.source_read_only)
                          .map((item) => item.record_id),
                      )
                    }
                  >
                    Select all
                  </Button>
                  <Button variant="ghost" onClick={() => setSelected([])}>
                    Clear
                  </Button>
                  <Button onClick={validateSelected} disabled={loading}>
                    <ClipboardCheck size={16} /> Recheck validation
                  </Button>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th></th>
                        <th>Name</th>
                        {isEsi ? (
                          <>
                            <th>Employee code</th>
                            <th>Aadhaar</th>
                            <th>DOB</th>
                            <th>DOJ</th>
                            <th>Mobile</th>
                            <th>Gender</th>
                          </>
                        ) : (
                          <>
                            <th>Emp code</th>
                            <th>UAN No</th>
                            <th>Aadhar Number</th>
                            <th>DOB</th>
                            <th>DOJ</th>
                            <th>Basic Wage</th>
                            <th>Father Name</th>
                            <th>Marital Status</th>
                            <th>Husband/Wife</th>
                            <th>Gender</th>
                            <th>Mobile</th>
                            <th>Location</th>
                            <th>PF No</th>
                          </>
                        )}
                        <th>Status</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {queueRecords.map((item) => (
                        <tr key={item.record_id}>
                          <td>
                            <input
                              type="checkbox"
                              disabled={item.source_read_only}
                              title={
                                item.source_read_only
                                  ? "Source API rows are read-only until accepted into the local queue."
                                  : "Select record"
                              }
                              checked={selected.includes(item.record_id)}
                              onChange={(event) =>
                                setSelected((current) =>
                                  event.target.checked
                                    ? [...current, item.record_id]
                                    : current.filter(
                                        (id) => id !== item.record_id,
                                      ),
                                )
                              }
                            />
                          </td>
                          <td>
                            <strong>{item.name}</strong>
                            <small>
                              {item.company}
                              {item.deputed_company
                                ? ` · ${item.deputed_company}`
                                : ""}
                            </small>
                          </td>
                          {isEsi ? (
                            <>
                              <td className={!item.employee_code ? "missing-cell" : ""}>{item.employee_code || "—"}</td>
                              <td className={!item.aadhaar ? "missing-cell" : ""}>{item.aadhaar || "—"}</td>
                              <td className={!item.dob ? "missing-cell" : ""}>{item.dob || "—"}</td>
                              <td className={!item.doj ? "missing-cell" : ""}>{item.doj || "—"}</td>
                              <td className={!item.mobile ? "missing-cell" : ""}>{item.mobile || "—"}</td>
                              <td className={!item.gender ? "missing-cell" : ""}>{item.gender || "—"}</td>
                            </>
                          ) : (
                            <>
                              <td>{item.emp_code}</td>
                              <td className={item.missing_required_fields?.includes("uan") ? "missing-cell" : ""}>{item.uan || "—"}</td>
                              <td className={item.missing_required_fields?.includes("aadhaar") ? "missing-cell" : ""}>{item.aadhaar || "—"}</td>
                              <td className={item.missing_required_fields?.includes("dob") ? "missing-cell" : ""}>{formatSourceDate(item.dob)}</td>
                              <td className={item.missing_required_fields?.includes("doj") ? "missing-cell" : ""}>{formatSourceDate(item.doj)}</td>
                              <td className={item.missing_required_fields?.includes("basic_wage") ? "missing-cell" : ""}>{item.basic_wage === "" || item.basic_wage == null ? "—" : item.basic_wage}</td>
                              <td>{item.father_name || "—"}</td>
                              <td>{item.marital_status || "—"}</td>
                              <td>{item.husband_wife_name || item.spouse_name || "—"}</td>
                              <td>{item.gender || "—"}</td>
                              <td>{item.mobile || "—"}</td>
                              <td>{item.location || "—"}</td>
                              <td>{item.pf_number || "—"}</td>
                            </>
                          )}
                          <td>
                            <Status
                              tone={recordStatusTone(item.status)}
                            >
                              {recordStatusLabel(item.status)}
                            </Status>
                            {item.duplicate_count > 0 && (
                              <small className="record-source">Seen {item.duplicate_count} duplicate time{item.duplicate_count === 1 ? "" : "s"}; no duplicate submission.</small>
                            )}
                            {item.missing_required_fields?.length > 0 && (
                              <small className="missing-fields">
                                Missing: {item.missing_required_fields.join(", ")}
                              </small>
                            )}
                            {item.source_kind && (
                              <small className="record-source">
                                {item.source_kind}
                                {item.converted ? " · Converted" : ""}
                              </small>
                            )}
                            {item.validation_errors?.length > 0 && (
                              <small className="missing-fields">
                                Validation: {item.validation_errors.map((error) => error.message).join("; ")}
                              </small>
                            )}
                          </td>
                          <td>
                            <div className="record-actions">
                              {isEsi && <EsiRecordDetails record={item} />}
                              <Button
                                type="button"
                                variant="ghost"
                                className="row-action"
                                onClick={() => setTimelineRecordId(item.record_id)}
                              >
                                Timeline
                              </Button>
                              {can("runs.start") && (isEsi ? ["ESI_FAILED", "ESI_VALIDATION_FAILED", "ESI_RECOVERY_REQUIRED"].includes(item.status) : ["FAILED", "VALIDATION_FAILED", "API_VALIDATION_ERROR", "RECOVERY_REQUIRED"].includes(item.status)) && (
                                <Button type="button" variant="ghost" className="row-action retry-action" onClick={() => retryRecord(item)} disabled={loading}>
                                  Retry
                                </Button>
                              )}
                              {can("runs.start") && (isEsi ? item.status === "ESI_UPDATE_REVIEW" : item.status === "UPDATE_REVIEW") && (
                                <Button type="button" variant="ghost" className="row-action retry-action" onClick={() => retryRecord(item, true)} disabled={loading}>
                                  Approve &amp; retry
                                </Button>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!queueRecords.length && (
                    <div className="empty">
                      {queueFilter === "all"
                        ? "No records in this company inbox."
                        : `No ${queueFilter} workflows for this scope.`}
                    </div>
                  )}
                </div>
                {timelineRecord && (
                  <div className="record-timeline" role="region" aria-label={`Timeline for ${timelineRecord.name || "record"}`}>
                    <div className="record-timeline-heading">
                      <div>
                        <span className="section-kicker">Record timeline</span>
                        <h3>{timelineRecord.name || "Employee record"}</h3>
                        <p>{timelineRecord.uan || timelineRecord.emp_code || timelineRecord.record_id}</p>
                      </div>
                      <div className="record-timeline-actions">
                        <Status tone={recordStatusTone(timelineRecord.status)}>{recordStatusLabel(timelineRecord.status)}</Status>
                        <Button type="button" variant="ghost" onClick={() => setTimelineRecordId("")} aria-label="Close record timeline"><X size={15} /></Button>
                      </div>
                    </div>
                    <div className="record-timeline-summary">
                      <span><strong>Attempts</strong>{timelineRecord.attempt_count || 0}</span>
                      <span><strong>Retries</strong>{timelineRecord.retry_count || 0}</span>
                      <span><strong>First seen</strong>{timelineRecord.first_seen_at ? new Date(timelineRecord.first_seen_at).toLocaleString() : "—"}</span>
                      <span><strong>Last seen</strong>{timelineRecord.last_seen_at ? new Date(timelineRecord.last_seen_at).toLocaleString() : "—"}</span>
                    </div>
                    {timelineRecord.pending_update && (
                      <div className="timeline-warning"><AlertTriangle size={15} /> New data for this successful record is waiting for explicit approval. The existing successful submission was kept unchanged.</div>
                    )}
                    <div className="timeline-list">
                      {(timelineRecord.status_history || []).slice().reverse().map((event, index) => (
                        <div className="timeline-event" key={`${event.timestamp || "event"}-${event.event || "status"}-${index}`}>
                          <span className="timeline-dot" />
                          <div><strong>{recordStatusLabel(event.status || event.event)}</strong><p>{event.message || event.event || "Status updated"}</p><small>{event.timestamp ? new Date(event.timestamp).toLocaleString() : "Timestamp unavailable"}{event.source ? ` · ${event.source}` : ""}</small></div>
                        </div>
                      ))}
                      {!timelineRecord.status_history?.length && <div className="empty compact">No status history has been recorded yet.</div>}
                    </div>
                    {can("runs.start") && (
                      <div className="record-timeline-footer">
                        { ["FAILED", "VALIDATION_FAILED", "API_VALIDATION_ERROR", "RECOVERY_REQUIRED"].includes(timelineRecord.status) && <Button type="button" onClick={() => retryRecord(timelineRecord)}>Retry record</Button> }
                        { timelineRecord.status === "UPDATE_REVIEW" && <Button type="button" onClick={() => retryRecord(timelineRecord, true)}>Approve update &amp; retry</Button> }
                        { ["SUCCESS", "COMPLETED", "Registered - Pending Approval"].includes(timelineRecord.status) && !timelineRecord.pending_update && <span className="timeline-immutable">Successful records are immutable; repeated input is skipped safely.</span> }
                      </div>
                    )}
                  </div>
                )}
              </div>
            </section>
            <section>
              <div className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>Live process</h2>
                    <p>
                      Every state transition is recorded for operations review.
                    </p>
                  </div>
                  <Activity size={19} className="panel-icon" />
                </div>
                <div className="run-banner">
                  <Status
                    tone={
                      run &&
                      (run.status === "HANDOVER_REQUIRED" ||
                        run.status === "FAILED")
                        ? "red"
                        : "purple"
                    }
                  >
                    {run ? run.status : "READY"}
                  </Status>
                  <strong>
                    {run
                      ? run.current_employee || "Preparing run"
                      : "No run started"}
                  </strong>
                  <span>
                    {run
                      ? (run.completed || 0) +
                        "/" +
                        (run.total || 0) +
                        " completed"
                      : "Select validated employees to begin"}
                  </span>
                </div>
                <pre className="event-log">
                  {run && run.events
                    ? run.events
                        .map(
                          (item) =>
                            item.timestamp +
                            "  " +
                            item.event +
                            "  " +
                            (item.message || ""),
                        )
                        .join("\n")
                    : "Run events will appear here."}
                </pre>
                {run && run.handover_email && (
                  <div
                    className={
                      "email-status " +
                      (run.handover_email.sent ? "sent" : "not-sent")
                    }
                  >
                    <Mail size={16} />
                    <div>
                      <strong>
                        {run.handover_email.sent
                          ? "Failure email sent"
                          : "Failure email not sent"}
                      </strong>
                      <span>
                        {run.handover_email.sent
                          ? "SMTP accepted the consolidated handover notification and evidence attachments."
                          : run.handover_email.error ||
                            "Configure SMTP settings to enable delivery; local handover remains available."}
                      </span>
                      {!run.handover_email.sent &&
                        run.handover_email.attempted && (
                          <Button variant="ghost" onClick={retryHandoverEmail}>
                            Retry email only
                          </Button>
                        )}
                    </div>
                  </div>
                )}
              </div>
              <div className="panel" id="error-handover-panel">
                <div className="panel-heading">
                  <div>
                    <h2>Error handover &amp; proof</h2>
                    <p>Failed rows, reasons, and evidence stay linked.</p>
                  </div>
                  <FileWarning size={19} className="panel-icon" />
                </div>
                {errors.length ? (
                  errors.map((error) => (
                    <div
                      className="error-row"
                      key={String(error.row) + error.message}
                    >
                      <div>
                        <Status tone="red">{error.category}</Status>
                        <strong>
                          Row {error.row} · {error.employee_name}
                        </strong>
                        <small>{error.workflow_step}</small>
                      </div>
                      <p>{error.message}</p>
                      <div className="proof-links">
                        {evidence
                          .filter((file) => file.source !== "logs")
                          .map((file) => (
                            <a
                              key={file.url}
                              href={file.url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              View {file.name}
                            </a>
                          ))}
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="empty compact">
                    No errors recorded for this run.
                  </div>
                )}
              </div>
            </section>
          </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

export default App;
