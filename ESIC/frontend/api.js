const config = window.ESI_CONFIG || {};

export const ESI_API_BASE_URL = String(config.apiBaseUrl || '/api/esi').replace(/\/$/, '');

async function request(path, options = {}) {
  const response = await fetch(`${ESI_API_BASE_URL}${path}`, {
    ...options,
    headers: { Accept: 'application/json', ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.message || `ESI API request failed (${response.status})`);
  }
  return payload;
}

export function scopeQuery(scope) {
  const params = new URLSearchParams({
    group_id: scope.groupId || '',
    company_id: scope.companyId || '',
    deputed_company_id: scope.deputedCompanyId || '',
  });
  return params.toString();
}

export const esiApi = {
  async health() { return request('/health'); },
  async scope(scope) { return request(`/scope?${scopeQuery(scope)}`); },
  async queue(scope, status = '') {
    const suffix = status ? `&status=${encodeURIComponent(status)}` : '';
    return request(`/records?${scopeQuery(scope)}${suffix}`);
  },
  async audit(scope) { return request(`/audit?${scopeQuery(scope)}`); },
  async reports(scope) { return request(`/reports?${scopeQuery(scope)}`); },
  async start(scope) {
    return request('/monitor/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(scope) });
  },
};
