// Turns what the API returned for each block into the sections, charts and tables of a report. Pure: no network, no DOM.
import { formatDuration } from '../text.js';
import { BLOCK_LABELS, filterValues } from './config.js';

export const TICKET_FIELDS = {
  customer: 'company_name', priority: 'priority', ticketStatus: 'status', ticketType: 'ticket_type', issueType: 'issue_type', subIssueType: 'sub_issue_type',
};
const SEVERITY = { CRITICAL: 0, WARNING: 1, OK: 2 };
// A row matches a filter if its field equals any of the filter's values, ignoring case; a blank filter matches everything.
const matches = (row, key, filter) => {
  const wanted = filterValues(filter);
  return !wanted.length || wanted.some((w) => String(row[key] ?? '').toLocaleLowerCase() === w.toLocaleLowerCase());
};
const hasValue = (filter, value) => { const wanted = filterValues(filter); return !wanted.length || wanted.includes(value); };
// "Acme", "Acme, Zed", or "Acme, Zed +2 more": how a filter reads in a heading or a note.
export const describeFilter = (filter) => {
  const v = filterValues(filter);
  return v.length > 2 ? `${v[0]}, ${v[1]} +${v.length - 2} more` : v.join(', ');
};
export const countBy = (rows, field) => {
  const grouped = new Map();
  for (const row of rows) {
    const value = String(row[field] ?? '').trim() || 'Unclassified';
    grouped.set(value, (grouped.get(value) || 0) + 1);
  }
  return [...grouped].map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
};
const meanHours = (rows, field) => {
  const values = rows.map((r) => r[field]).filter((v) => v != null && v !== '' && Number.isFinite(Number(v))).map(Number);
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
};
const healthLabel = (h) => (h === 'OK' ? 'OK' : String(h).charAt(0) + String(h).slice(1).toLowerCase());
const shorten = (text, n) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);
const flagCategory = (c) => String(c ?? 'session_failure').replace(/_/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());
const all = (v) => describeFilter(v) || 'All';

const scopeLabel = (block, response) => (block.filters.patchScope ? (response?.clients ?? response?.patches?.clients ?? []).find((o) => o.key === block.filters.patchScope)?.label : undefined);
export function blockTitle(block, response) {
  if (block.title) return block.title;
  const extra = describeFilter(block.filters.customer) || scopeLabel(block, response);
  return extra ? `${BLOCK_LABELS[block.type]} — ${extra}` : BLOCK_LABELS[block.type];
}

// Each returns { section, tables, warning }: the charts and tiles, the detail tables (which only reach the report as an annex), or why there is nothing.
const nothing = (warning) => ({ section: null, tables: [], warning });

function tickets(block, response, limit, period) {
  if (!response) return nothing('unavailable.');
  const f = block.filters;
  let rows = response.tickets?.rows ?? [];
  for (const [filter, field] of Object.entries(TICKET_FIELDS)) rows = rows.filter((r) => matches(r, field, f[filter]));
  if (!rows.length) return nothing('no records match the selected filters.');
  return {
    warning: null,
    section: {
      title: blockTitle(block, response),
      notes: [`Period: ${period.from} to ${period.to}`, `Customer: ${all(f.customer)} | Priority: ${all(f.priority)} | Status: ${all(f.ticketStatus)} | Type: ${all(f.ticketType)}`, `Issue: ${all(f.issueType)} | Sub-issue: ${all(f.subIssueType)}`],
      items: [
        { kpis: [{ label: 'Ticket Count', value: rows.length }, { label: 'Avg Resolution Time', value: formatDuration(meanHours(rows, 'resolution_hours')) }, { label: 'Avg Response Time', value: formatDuration(meanHours(rows, 'response_hours')) }] },
        { chart: 'donut', title: 'Tickets by Priority', data: countBy(rows, 'priority') },
        { chart: 'donut', title: 'Tickets by Type / Category', data: countBy(rows, 'ticket_type') },
        { chart: 'donut', title: 'Tickets by Issue Type', data: countBy(rows, 'issue_type') },
        { chart: 'bar', title: 'Tickets by Sub-Issue', data: countBy(rows, 'sub_issue_type') },
      ],
    },
    tables: [{ name: 'Ticket detail', note: `First ${Math.min(rows.length, limit)} of ${rows.length} tickets`, columns: ['Ticket', 'Customer', 'Priority', 'Status', 'Title'],
      rows: rows.slice(0, limit).map((r) => [r.ticket_number, r.company_name, r.priority, r.status, r.title]) }],
  };
}

function patchCategories(block, patches) {
  return (patches?.types ?? []).filter((t) => hasValue(block.filters.patchType, t.key)).flatMap((t) => (t.categories ?? []).map((c) => ({ ...c, type: t.label })));
}

function patches(block, response) {
  if (!response) return nothing('unavailable.');
  const cats = patchCategories(block, response);
  if (!cats.length) return nothing('no matching source records.');
  const installed = cats.reduce((s, c) => s + (Number(c.installed) || 0), 0);
  const missing = cats.reduce((s, c) => s + (Number(c.missing) || 0), 0);
  const overall = response.overall?.current;
  return {
    warning: null,
    section: {
      title: blockTitle(block, response),
      notes: [`Patch type: ${all(block.filters.patchType)} | Client: ${scopeLabel(block, response) ?? 'All'}`, 'Based on recorded source inventories. Unavailable devices may make coverage incomplete.'],
      items: [
        { kpis: [{ label: 'Installed patches', value: installed }, { label: 'Missing patches', value: missing }, { label: 'Patch entries', value: installed + missing },
          ...(!filterValues(block.filters.patchType).length && overall != null ? [{ label: 'Overall score', value: `${overall}%` }] : [])] },
        { chart: 'bar', title: 'Missing Patches by Category', data: cats.map((c) => ({ label: `${c.type}: ${c.category}`, value: Number(c.missing) || 0 })) },
        { chart: 'bar', title: 'Patch Compliance by Category', percent: true, data: cats.filter((c) => c.compliance != null).map((c) => ({ label: `${c.type}: ${c.category}`, value: Number(c.compliance) })) },
      ],
    },
    tables: [{ name: 'Patch compliance details', note: `${cats.length} categories`, columns: ['Type', 'Category', 'Installed', 'Missing', 'Compliance'],
      rows: cats.map((c) => [c.type, c.category, c.installed, c.missing, c.compliance == null ? 'N/A' : `${c.compliance}%`]) }],
  };
}

function patchDetails(block, response, limit) {
  if (!response?.patches) return nothing('unavailable.');
  const rows = response.details ?? [];
  if (!rows.length) return nothing('no records returned.');
  return {
    warning: null,
    section: { title: blockTitle(block, response.patches), notes: [`Patch type: ${all(block.filters.patchType)} | ${rows.length} entries`], items: [{ chart: 'bar', title: 'Patch Details by Category', data: countBy(rows, 'patch_type') }] },
    tables: [{ name: 'Patch inventory', note: `First ${Math.min(rows.length, limit)} of ${rows.length} entries`, columns: ['Device', 'Patch', 'KB', 'Missing', 'Type'],
      rows: rows.slice(0, limit).map((r) => [r.device ?? r.device_id, r.title, r.kb, r.is_missing ? 'Yes' : 'No', r.patch_type]) }],
  };
}

function serverHealth(block, response, limit) {
  if (!response) return nothing('unavailable.');
  const rows = (response.servers ?? []).filter((r) => matches(r, 'customer_name', block.filters.customer) && matches(r, 'network_status', block.filters.agentStatus));
  if (!rows.length) return nothing('no matching records.');
  return {
    warning: null,
    section: {
      title: blockTitle(block, response), notes: [`Customer: ${all(block.filters.customer)} | Network: ${all(block.filters.agentStatus)}`],
      items: [
        { kpis: [{ label: 'Endpoints', value: rows.length }, { label: 'Active agents', value: rows.filter((r) => r.is_active === true).length }, { label: 'Infected', value: rows.filter((r) => r.infected === true).length }] },
        { chart: 'donut', title: 'Network Status', data: countBy(rows, 'network_status') },
        { chart: 'bar', title: 'Endpoint Operating Systems', data: countBy(rows, 'os_type') },
      ],
    },
    tables: [{ name: 'Endpoint detail', note: `First ${Math.min(rows.length, limit)} of ${rows.length} endpoints`, columns: ['Customer', 'Device', 'Network', 'Agent', 'Data age (h)'],
      rows: rows.slice(0, limit).map((r) => [r.customer_name, r.computer_name, r.network_status, r.agent_version, r.data_age_hours == null ? 'N/A' : Number(r.data_age_hours).toFixed(1)]) }],
  };
}

function backups(block, response, limit) {
  if (!response) return nothing('unavailable.');
  if (!response.available) return nothing('the Cove schema was not found.');
  const rows = (response.rows ?? []).filter((r) => matches(r, 'customer', block.filters.customer) && matches(r, 'health', block.filters.backupHealth))
    .sort((a, b) => (SEVERITY[a.health] ?? 3) - (SEVERITY[b.health] ?? 3) || (b.days_since_success ?? -1) - (a.days_since_success ?? -1));
  if (!rows.length) return nothing(response.loaded ? 'no matching devices.' : 'no backup data has been collected yet.');
  const count = (h) => rows.filter((r) => r.health === h).length;
  const flags = rows.flatMap((r) => (r.flags ?? []).map((fl) => ({ category: flagCategory(fl.category) })));
  return {
    warning: null,
    section: {
      title: blockTitle(block, response),
      notes: [`Customer: ${all(block.filters.customer)} | Health: ${all(block.filters.backupHealth)}`, 'Current state from the latest Cove snapshots. Health and red flags are set by the Cove collector.'],
      items: [
        { kpis: [{ label: 'Devices', value: rows.length }, { label: 'OK', value: count('OK') }, { label: 'Warning', value: count('WARNING') }, { label: 'Critical', value: count('CRITICAL') }, { label: 'Red flags', value: rows.reduce((s, r) => s + (Number(r.red_flag_count) || 0), 0) }] },
        { chart: 'donut', title: 'Devices by Health', data: countBy(rows.map((r) => ({ ...r, health_label: healthLabel(r.health) })), 'health_label') },
        { chart: 'donut', title: 'Devices by Type', data: countBy(rows, 'device_type') },
        ...(flags.length ? [{ chart: 'bar', title: 'Red Flags by Category', data: countBy(flags, 'category') }] : []),
      ],
    },
    tables: [{ name: 'Backup detail', note: `Worst first, first ${Math.min(rows.length, limit)} of ${rows.length} devices`, columns: ['Customer', 'Device', 'Health', 'Days since success', 'Red flags'],
      rows: rows.slice(0, limit).map((r) => [r.customer, r.device_name, healthLabel(r.health), r.days_since_success == null ? 'N/A' : r.days_since_success, r.flags?.length ? shorten(r.flags.map((fl) => fl.flag).join('; '), 90) : 'None']) }],
  };
}

const EXTRACTORS = { tickets, patches, 'patch-details': patchDetails, 'server-health': serverHealth, backups };
const annexLetter = (i) => (i < 26 ? String.fromCharCode(65 + i) : String(i + 1));

// { sections, annex, warnings } for a validated config, the data fetched for each block (keyed by block id) and the period used.
// Without config.annex the detail tables are left out altogether: the report is the charts and tiles.
export function extractReport(config, data, period) {
  const sections = [], tables = [], warnings = [];
  for (const block of config.blocks) {
    const result = EXTRACTORS[block.type](block, data[block.id], config.maxRows, period);
    if (result.warning) warnings.push(`${blockTitle(block, data[block.id])}: ${result.warning}`);
    if (!result.section) continue;
    sections.push(result.section);
    for (const table of result.tables) tables.push({ ...table, section: result.section.title });
  }
  const annex = config.annex ? tables.map((t, i) => ({ title: `Annex ${annexLetter(i)}: ${t.name}`, notes: [`Section: ${t.section}`, t.note], items: [{ columns: t.columns, rows: t.rows }] })) : [];
  return { sections, annex, warnings };
}
