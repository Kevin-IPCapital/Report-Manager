// SolarWinds N-central REST API client.
//
// Auth model (confirmed live against our instance, contradicts vendor docs in places):
//   - The only credential ever sent is the long-lived NCENTRAL_API_TOKEN.
//   - POST /api/auth/authenticate with that token as the Bearer header (no body)
//     returns a short-lived access token nested under tokens.access.token.
//   - We re-authenticate from scratch on expiry instead of using /api/auth/refresh.
//
// Rate limiting: the API 429s heavily above ~5 concurrent requests, so all
// fan-out work must go through withConcurrencyLimit() rather than Promise.all.

const BASE_URL = process.env.NCENTRAL_API_BASE_URL;
const API_TOKEN = process.env.NCENTRAL_API_TOKEN;

const REQUEST_TIMEOUT_MS = 15_000;
const TOKEN_REFRESH_MARGIN_MS = 60_000;
const DEFAULT_CONCURRENCY = 5;

const CUSTOMER_LIST_TTL_MS = 15 * 60_000;
const BULK_PULL_TTL_MS = 45 * 60_000;

let cachedAccessToken = null; // { token, expiresAt }
let inFlightAuth = null; // Promise, so concurrent callers share one authenticate() call

const cache = new Map(); // key -> { value, expiresAt }

function assertConfigured() {
  if (!BASE_URL || !API_TOKEN) {
    throw new Error('Missing NCENTRAL_API_BASE_URL or NCENTRAL_API_TOKEN in .env');
  }
}

async function fetchWithTimeout(url, options) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function authenticate() {
  assertConfigured();

  const res = await fetchWithTimeout(`${BASE_URL}/api/auth/authenticate`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${API_TOKEN}` },
  });
  const body = await res.json().catch(() => ({}));

  if (!res.ok) {
    const err = new Error(
      `N-central authenticate failed (${res.status}): ${body.message || JSON.stringify(body)}`
    );
    err.status = res.status;
    err.body = body;
    if (res.status === 401 && /DmsLoginException/.test(body.message || '')) {
      err.message +=
        ' — this is an N-central-side role/permission problem on the API user, not a client auth-format bug.';
    }
    throw err;
  }

  const access = body.tokens?.access;
  if (!access?.token) {
    throw new Error(`Unexpected /authenticate response shape: ${JSON.stringify(body)}`);
  }

  cachedAccessToken = {
    token: access.token,
    expiresAt: Date.now() + access.expirySeconds * 1000,
  };
  return cachedAccessToken.token;
}

async function getAccessToken() {
  if (cachedAccessToken && cachedAccessToken.expiresAt - TOKEN_REFRESH_MARGIN_MS > Date.now()) {
    return cachedAccessToken.token;
  }
  if (!inFlightAuth) {
    inFlightAuth = authenticate().finally(() => {
      inFlightAuth = null;
    });
  }
  return inFlightAuth;
}

// Low-level authenticated request. No automatic retry on 429/failure by design —
// callers get a fast, catchable error instead of a silent multi-second stall.
async function request(path, options = {}) {
  const token = await getAccessToken();
  const res = await fetchWithTimeout(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      ...options.headers,
      Authorization: `Bearer ${token}`,
    },
  });

  if (res.status === 429) {
    const err = new Error(`Rate limited (429) on ${path}`);
    err.status = 429;
    throw err;
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    const err = new Error(`Request failed (${res.status}) on ${path}: ${body}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// Walks pageNumber 2..totalPages, fetching sequentially per endpoint but callers
// can still run multiple *different* paginated resources concurrently via
// withConcurrencyLimit.
async function paginate(path, { pageSize = 500, params = {} } = {}) {
  const buildUrl = (pageNumber) => {
    const qs = new URLSearchParams({ ...params, pageSize: String(pageSize), pageNumber: String(pageNumber) });
    return `${path}?${qs.toString()}`;
  };

  const first = await request(buildUrl(1));
  const items = [...first.data];

  for (let pageNumber = 2; pageNumber <= first.totalPages; pageNumber++) {
    const page = await request(buildUrl(pageNumber));
    items.push(...page.data);
  }

  return { items, totalItems: first.totalItems, totalPages: first.totalPages };
}

// Runs `worker` over `items` with at most `limit` in flight at once.
async function withConcurrencyLimit(items, worker, limit = DEFAULT_CONCURRENCY) {
  const results = new Array(items.length);
  let nextIndex = 0;

  async function runNext() {
    const i = nextIndex++;
    if (i >= items.length) return;
    results[i] = await worker(items[i], i);
    return runNext();
  }

  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, runNext));
  return results;
}

function getCached(key, ttlMs, loader) {
  const hit = cache.get(key);
  if (hit && hit.expiresAt > Date.now()) return hit.value;

  const value = loader().then(
    (v) => {
      cache.set(key, { value: Promise.resolve(v), expiresAt: Date.now() + ttlMs });
      return v;
    },
    (err) => {
      cache.delete(key); // don't cache failures
      throw err;
    }
  );
  cache.set(key, { value, expiresAt: Date.now() + ttlMs });
  return value;
}

async function listOrgUnits() {
  const { items } = await paginate('/api/org-units', { pageSize: 500 });
  return items;
}

// orgUnitType === "CUSTOMER" rows only; /api/org-units already has no
// historical/deleted rows, so no extra active-only filtering is needed.
function getCustomers() {
  return getCached('customers', CUSTOMER_LIST_TTL_MS, async () => {
    const orgUnits = await listOrgUnits();
    return orgUnits.filter((ou) => ou.orgUnitType === 'CUSTOMER');
  });
}

// Every device under orgUnitId, including all descendant sites (each row is
// tagged with its own siteId/siteName) — no per-site call needed.
function getDevicesForOrgUnit(orgUnitId) {
  return getCached(`devices:${orgUnitId}`, BULK_PULL_TTL_MS, async () => {
    const { items } = await paginate(`/api/org-units/${orgUnitId}/devices`, { pageSize: 500 });
    return items;
  });
}

function getCustomProperties(orgUnitId) {
  return getCached(`custom-properties:${orgUnitId}`, BULK_PULL_TTL_MS, async () => {
    const { items } = await paginate(`/api/org-units/${orgUnitId}/custom-properties`, { pageSize: 100 });
    return items;
  });
}

// Deployment-wide, flat list — orgUnitId query param is silently ignored by the
// API, and there is no "enabled" flag on these rows.
function getDeviceFilters() {
  return getCached('device-filters', CUSTOMER_LIST_TTL_MS, async () => {
    const { items } = await paginate('/api/device-filters', { pageSize: 500 });
    return items;
  });
}

module.exports = {
  getAccessToken,
  request,
  paginate,
  withConcurrencyLimit,
  listOrgUnits,
  getCustomers,
  getDevicesForOrgUnit,
  getCustomProperties,
  getDeviceFilters,
};
