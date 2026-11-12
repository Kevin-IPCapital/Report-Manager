// A report is a list of BLOCKS. Each block is one kind of section with its own filters, so one report can hold "Tickets for Acme" and
// "Tickets for Zed" side by side. Version 1 templates (one flat set of sections and filters) are migrated when read.
// These rules are repeated on the server (backend/api/reports.py); both are tested against report/template-cases.json.
export const VERSION = 2;
// Change this number to control when a populated filter switches from a dropdown to search/input.
export const FILTER_DROPDOWN_LIMIT = 50;
export const BLOCK_TYPES = ['tickets', 'patches', 'patch-details', 'server-health', 'backups'];
export const BLOCK_LABELS = {
  tickets: 'Client Tickets', patches: 'Patch Compliance', 'patch-details': 'Patch Details', 'server-health': 'Endpoint Health', backups: 'Backup Health',
};
// The filters each kind of block understands. patchScope is "client:<id>" or "org:<id>", as on the Patches page.
export const BLOCK_FILTERS = {
  tickets: ['customer', 'priority', 'ticketStatus', 'ticketType', 'issueType', 'subIssueType'],
  patches: ['patchType', 'patchScope'],
  'patch-details': ['patchType', 'patchScope'],
  'server-health': ['customer', 'agentStatus'],
  backups: ['customer', 'backupHealth'],
};
export const FILTER_LABELS = {
  customer: 'Customer', priority: 'Priority', ticketStatus: 'Ticket status', ticketType: 'Ticket type', issueType: 'Issue type', subIssueType: 'Sub-issue type',
  patchType: 'Patch type', patchScope: 'Client', agentStatus: 'Endpoint network state', backupHealth: 'Backup health',
};
export const MAX_BLOCKS = 20;
// A report's default period can be a duration instead of dates: "the last 3 months" is worked out from the day the report runs.
export const DURATION_UNITS = { days: 3660, weeks: 520, months: 120 };   // the longest amount for each unit
export const MAX_FILTER_VALUES = 20;   // one filter can hold several values (a row matches if it has any of them), except the client scope
const TITLE = 'IP Capital Management Report';
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const WEIGHT_KEY = /^[a-z0-9-]+(:.{0,150})?$/;
const BLOCK_ID = /^[A-Za-z0-9_-]{1,40}$/;

// The values a filter holds, as a list: "" gives none, "Acme" gives one, a list gives its (non-blank) items.
export const filterValues = (v) => (Array.isArray(v) ? v : v ? [v] : []).filter((x) => typeof x === 'string' && x.trim() !== '');

export const newBlock = (type, id) => ({ id, type, title: '', filters: Object.fromEntries(BLOCK_FILTERS[type].map((k) => [k, ''])) });
export const DEFAULT_CONFIG = {
  version: VERSION, title: TITLE, annex: false, maxRows: 200, period: { from: '', to: '' }, weights: {},
  blocks: [newBlock('tickets', 'b1'), newBlock('patches', 'b2'), newBlock('server-health', 'b3')],
};

function migrateV1(input) {
  const order = [['tickets', 'tickets'], ['patches', 'patches'], ['server-health', 'server-health'], ['backups', 'backups'], ['patch-details', 'patch-details']];
  const sections = input.sections && typeof input.sections === 'object' ? input.sections : {};
  const old = input.filters && typeof input.filters === 'object' ? input.filters : {};
  const blocks = order.filter(([key]) => sections[key] === true).map(([key, type], i) => ({
    id: `b${i + 1}`, type, title: '', filters: Object.fromEntries(BLOCK_FILTERS[type].filter((k) => k in old && k !== 'patchScope').map((k) => [k, old[k]])),
  }));
  return { version: VERSION, title: input.title, annex: false, maxRows: input.maxRows, period: { from: old.from ?? '', to: old.to ?? '' }, weights: {}, blocks };
}

// One filter: text as given, or a list of up to 20 values (trimmed, blanks dropped, repeats ignoring case dropped). One value becomes
// plain text and none becomes blank, so a single choice always looks the same whichever way it was entered.
function cleanFilter(key, value) {
  if (typeof value === 'string') {
    if (value.length > 200) throw new Error(`Invalid ${key} filter.`);
    return value;
  }
  if (!Array.isArray(value)) throw new Error(`Invalid ${key} filter.`);
  if (key === 'patchScope') throw new Error('The client filter takes one value.');
  if (value.length > MAX_FILTER_VALUES) throw new Error(`A filter can have at most ${MAX_FILTER_VALUES} values.`);
  const seen = new Set(), kept = [];
  for (const item of value) {
    if (typeof item !== 'string' || item.length > 200) throw new Error(`Invalid ${key} filter.`);
    const text = item.trim();
    if (text && !seen.has(text.toLowerCase())) { seen.add(text.toLowerCase()); kept.push(text); }
  }
  return kept.length === 0 ? '' : kept.length === 1 ? kept[0] : kept;
}

function cleanWeights(raw) {
  if (raw == null) return {};
  if (typeof raw !== 'object' || Array.isArray(raw) || Object.keys(raw).length > 500) throw new Error('Weights must be an object with at most 500 entries.');
  const out = {};
  for (const [key, value] of Object.entries(raw)) {
    if (!WEIGHT_KEY.test(key)) throw new Error(`Invalid weight key: ${key.slice(0, 40)}`);
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1000) throw new Error(`Weight for ${key} must be a number from 0 to 1000.`);
    out[key] = value;
  }
  return out;
}

function cleanPeriod(raw) {
  const period = { from: '', to: '' };
  if (raw != null && (typeof raw !== 'object' || Array.isArray(raw))) throw new Error('Invalid period.');
  for (const key of ['from', 'to']) {
    const value = raw?.[key] ?? '';
    if (typeof value !== 'string' || (value && !DATE.test(value))) throw new Error('Dates must use YYYY-MM-DD.');
    period[key] = value;
  }
  if (period.from && period.to && period.from > period.to) throw new Error('Start date must not be after end date.');
  const last = raw?.last;
  if (last != null) {
    if (typeof last !== 'object' || Array.isArray(last) || Object.keys(last).sort().join() !== 'amount,unit') throw new Error('Invalid duration.');
    if (!Object.hasOwn(DURATION_UNITS, last.unit)) throw new Error('A duration is in days, weeks or months.');
    if (!Number.isInteger(last.amount) || last.amount < 1 || last.amount > DURATION_UNITS[last.unit]) throw new Error(`A duration is from 1 to ${DURATION_UNITS[last.unit]} ${last.unit}.`);
    if (period.from || period.to) throw new Error('Choose either dates or a duration for the default period, not both.');
    period.last = { amount: last.amount, unit: last.unit };   // only present when the report has one
  }
  return period;
}

// The normalised template, or an Error saying what is wrong. Accepts version 1 and version 2.
export function validateTemplate(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('Invalid template.');
  if (input.version !== 1 && input.version !== VERSION) throw new Error('Unsupported report template version.');
  const t = input.version === 1 ? migrateV1(input) : input;
  if (!Array.isArray(t.blocks) || t.blocks.length < 1) throw new Error('Add at least one section.');
  if (t.blocks.length > MAX_BLOCKS) throw new Error(`A report can have at most ${MAX_BLOCKS} sections.`);
  const seen = new Set();
  const blocks = t.blocks.map((b, i) => {
    if (!b || typeof b !== 'object' || !BLOCK_TYPES.includes(b.type)) throw new Error('Unknown section type.');
    if (b.filters != null && (typeof b.filters !== 'object' || Array.isArray(b.filters))) throw new Error('Invalid filters.');
    const filters = {};
    for (const key of Object.keys(b.filters ?? {})) if (!BLOCK_FILTERS[b.type].includes(key)) throw new Error(`The ${key} filter does not apply to ${BLOCK_LABELS[b.type]}.`);
    for (const key of BLOCK_FILTERS[b.type]) filters[key] = cleanFilter(key, b.filters?.[key] ?? '');
    const title = b.title == null ? '' : b.title;
    if (typeof title !== 'string' || title.length > 120) throw new Error('A section title can have at most 120 characters.');
    let id = typeof b.id === 'string' && BLOCK_ID.test(b.id) && !seen.has(b.id) ? b.id : null;
    for (let n = i + 1; !id; n += 1) if (!seen.has(`b${n}`)) id = `b${n}`;
    seen.add(id);
    return { id, type: b.type, title: title.trim(), filters };
  });
  const title = typeof t.title === 'string' ? t.title.trim().slice(0, 120) : '';
  return {
    version: VERSION, title: title || TITLE, annex: t.annex === true,
    maxRows: Math.max(10, Math.min(500, Number.isInteger(t.maxRows) ? t.maxRows : 200)),
    period: cleanPeriod(t.period), weights: cleanWeights(t.weights), blocks,
  };
}
