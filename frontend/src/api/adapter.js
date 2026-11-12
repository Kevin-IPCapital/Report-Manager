// The ONLY file that knows how data is fetched and how tokens are handled.
const BASE_URL = '/api';

// Access token lives in memory only. The refresh token is an httpOnly cookie the browser manages.
let accessToken = null;
let onExpired = () => {};

export class AuthError extends Error {}

async function send(path, { method = 'GET', body, auth = true } = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-Requested-With': 'fetch', // required by the backend on cookie-using endpoints
      ...(auth && accessToken && { Authorization: `Bearer ${accessToken}` }),
    },
    body: body && JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new (res.status === 401 ? AuthError : Error)(data.error || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// Single-flight refresh: parallel 401s share one call (in this tab), and the Web Lock
// serialises refreshes across tabs. The server's leeway window covers whatever still slips through.
let refreshing = null;
const withLock = (fn) => (navigator.locks ? navigator.locks.request('report-manager-refresh', fn) : fn());

function refresh() {
  refreshing ??= withLock(() => send('/auth/refresh', { method: 'POST', auth: false }))
    .then(({ access, user }) => { accessToken = access; return user; })
    .catch((e) => { accessToken = null; throw e; })
    .finally(() => { refreshing = null; });
  return refreshing;
}

// Authenticated call: on 401, refresh once and retry once; if that fails the session is over.
async function request(path, opts) {
  try {
    return await send(path, opts);
  } catch (e) {
    if (!(e instanceof AuthError)) throw e;
    try { await refresh(); } catch { onExpired(); throw e; }
    return send(path, opts);
  }
}

export const api = {
  onSessionExpired: (fn) => { onExpired = fn; },

  // Called once on page load: the cookie (if any) is exchanged for an access token and the user.
  restoreSession: () => refresh().catch(() => null),

  async login(username, password) {
    const { access, user } = await send('/auth/login', { method: 'POST', body: { username, password }, auth: false });
    accessToken = access;
    return user;
  },

  async logout() {
    await send('/auth/logout', { method: 'POST', auth: false }).catch(() => {});
    accessToken = null;
  },

  // params: from, to (YYYY-MM-DD), and whichever of client, org, device, type, category, scope, weights the section takes (docs/08_API_REFERENCE.md).
  // The answer's shape differs by section; the same file documents each.
  getSection(section, params = {}) {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null)).toString();
    return request(`/sections/${section}${qs ? `?${qs}` : ''}`);
  },


  register: async (username, email, password) => {
    const { access, user } = await send('/auth/register', { method: 'POST', body: { username, email, password }, auth: false });
    accessToken = access;
    return user;
  },
  forgotPassword: (identifier) => send('/auth/forgot', { method: 'POST', body: { identifier }, auth: false }),
  resetPassword: (uid, token, password) => send('/auth/reset', { method: 'POST', body: { uid, token, password }, auth: false }),

  getFreshness: () => request('/freshness'),   // { checked_at, sources: [{ key, label, as_of, age_hours, state, failures_since, basis, enabled }] }: when each page's data was collected, open to everyone signed in
  getCollectionHealth: () => request('/collectors/health'),
  getCollectionExecution: () => request('/collectors/execution'),
  startCollection: (job) => request('/collectors/run', { method: 'POST', body: { job } }),
  getCollectorLatestLog: (job) => request(`/collectors/${encodeURIComponent(job)}/latest-log`),
  getClients: () => request('/clients'),
  getResolution: () => request('/clients/resolution'),   // { sources, proposals, clients, summary }, staff only
  resolveClients: (groups) => request('/clients/resolve', { method: 'POST', body: { groups } }), // { created, extended, errors }
  getAccess: () => request('/access'),               // { enforced, users: [{ username, name, is_staff, clients: [ids] }], clients: [{ id, name }] }, staff only
  saveAccess: (username, clients) => request(`/access/${encodeURIComponent(username)}`, { method: 'PUT', body: { clients } }),   // replaces that person's clients
  saveClientGroup: ({ id, ...fields }) => request(id ? `/client-groups/${id}` : '/client-groups', { method: id ? 'PUT' : 'POST', body: fields }),   // a named list of clients (or all of a company's): { name, all_clients, clients: [IDs] }, and for the platform owner { company }
  deleteClientGroup: (id) => request(`/client-groups/${id}`, { method: 'DELETE' }),
  createPerson: (fields) => request('/users', { method: 'POST', body: fields }),   // { username, email, first_name, last_name, client_groups: [IDs], is_staff }: no password; answers { setup_link, sent, valid_for_minutes, email }
  updatePerson: (username, fields) => request(`/users/${encodeURIComponent(username)}`, { method: 'PUT', body: fields }),   // only what changes: names, email, is_active, client_groups, (owner) is_staff
  newSetupLink: (username) => request(`/users/${encodeURIComponent(username)}/link`, { method: 'POST' }),   // a fresh link to choose a password
  getReports: () => request('/reports'),             // [{ id, name, shared, owner, mine, config, updated_at }]
  saveReport: ({ id, ...fields }) => request(id ? `/reports/${id}` : '/reports', { method: id ? 'PUT' : 'POST', body: fields }),
  deleteReport: (id) => request(`/reports/${id}`, { method: 'DELETE' }),
  getWeights: () => request('/weights'),             // { shared, user, effective }
  saveWeights: (scope, weights) => request('/weights', { method: 'PUT', body: { scope, weights } }), // replaces that set
  saveClient: ({ id, company, ...fields }) => request(id ? `/clients/${id}` : '/clients', { method: id ? 'PUT' : 'POST', body: fields }),   // the company is changed by claiming, never by editing
  claimClient: (id, company) => request(`/clients/${id}/claim`, { method: 'POST', body: company === undefined ? {} : { company } }),   // an administrator claims for their own company; the owner names the company, or null to take it back
  getCompanies: () => request('/companies'),                 // [{ id, name, members, clients }]: the owner sees all, an administrator their own
  createCompany: (name) => request('/companies', { method: 'POST', body: { name } }),
  setUserCompany: (username, company) => request(`/users/${encodeURIComponent(username)}/company`, { method: 'PUT', body: { company } }),   // owner only; null takes them out
};
