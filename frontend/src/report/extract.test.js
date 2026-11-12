import assert from 'node:assert/strict';
import { test } from 'node:test';
import { newBlock, validateTemplate } from './config.js';
import { blockTitle, countBy, describeFilter, extractReport } from './extract.js';

const period = { from: '2026-10-01', to: '2026-10-31' };
const ticket = (n, company, priority, resolution = null, response = null) => ({ ticket_number: `T${n}`, company_name: company, priority, status: 'Complete', ticket_type: 'Incident',
  issue_type: 'Network', sub_issue_type: 'VPN', title: `Ticket ${n}`, resolution_hours: resolution, response_hours: response });
const ticketData = { tickets: { rows: [ticket(1, 'Acme', 'High', 1, 0.5), ticket(2, 'Acme', 'Low', 3, 1.5), ticket(3, 'Zed', 'High', null, null)] } };
const config = (blocks, extra = {}) => validateTemplate({ version: 2, blocks, ...extra });
const kpi = (section, label) => section.items[0].kpis.find((k) => k.label === label).value;

test('two ticket blocks with different customers give two sections from the same data', () => {
  const cfg = config([{ type: 'tickets', filters: { customer: 'acme' } }, { type: 'tickets', filters: { customer: 'Zed' } }]);
  const data = { b1: ticketData, b2: ticketData };
  const { sections, warnings } = extractReport(cfg, data, period);
  assert.deepEqual(sections.map((s) => s.title), ['Client Tickets — acme', 'Client Tickets — Zed']);   // the filter's own text, so the match is case-insensitive
  assert.deepEqual(sections.map((s) => kpi(s, 'Ticket Count')), [2, 1]);
  assert.deepEqual(warnings, []);
});

test('ticket averages are shown as durations, and a missing average as a dash', () => {
  const { sections } = extractReport(config([{ type: 'tickets' }]), { b1: ticketData }, period);
  assert.equal(kpi(sections[0], 'Avg Resolution Time'), '2 hours');
  assert.equal(kpi(sections[0], 'Avg Response Time'), '1 hour');
  const none = extractReport(config([{ type: 'tickets', filters: { customer: 'Zed' } }]), { b1: ticketData }, period).sections[0];
  assert.equal(kpi(none, 'Avg Resolution Time'), '—');
  assert.match(none.notes[0], /Period: 2026-10-01 to 2026-10-31/);
});

test('a custom title wins over the generated one', () => {
  assert.equal(blockTitle({ type: 'tickets', title: 'Board pack', filters: { customer: 'Acme' } }), 'Board pack');
  assert.equal(blockTitle({ type: 'backups', title: '', filters: { customer: '' } }), 'Backup Health');
});

test('no matching rows, and missing data, are warnings that name the section', () => {
  const cfg = config([{ type: 'tickets', filters: { customer: 'Nobody' } }, { type: 'server-health' }]);
  const { sections, warnings } = extractReport(cfg, { b1: ticketData }, period);
  assert.deepEqual(sections, []);
  assert.deepEqual(warnings, ['Client Tickets — Nobody: no records match the selected filters.', 'Endpoint Health: unavailable.']);
});

test('without the annex option the report has no tables at all', () => {
  const { sections, annex } = extractReport(config([{ type: 'tickets' }]), { b1: ticketData }, period);
  assert.deepEqual(annex, []);
  assert.ok(sections[0].items.every((i) => !i.columns && !i.rows));
  assert.ok(sections[0].items.some((i) => i.chart));
});

test('with the annex option each detail table goes to its own annex page, in order, limited by maxRows', () => {
  const cfg = config([{ type: 'tickets' }, { type: 'server-health' }], { annex: true, maxRows: 10 });
  const servers = { servers: Array.from({ length: 14 }, (_, i) => ({ customer_name: 'Acme', computer_name: `PC${i}`, network_status: 'online', os_type: 'windows', agent_version: '1', is_active: true, infected: false, data_age_hours: 1.234 })) };
  const { sections, annex } = extractReport(cfg, { b1: ticketData, b2: servers }, period);
  assert.equal(sections.length, 2);
  assert.deepEqual(annex.map((a) => a.title), ['Annex A: Ticket detail', 'Annex B: Endpoint detail']);
  assert.deepEqual(annex[1].notes, ['Section: Endpoint Health', 'First 10 of 14 endpoints']);
  assert.equal(annex[1].items[0].rows.length, 10);
  assert.equal(annex[1].items[0].rows[0][4], '1.2');
  assert.ok(sections.every((s) => s.items.every((i) => !i.columns)));
});

test('patch compliance: the overall score only when it covers every type, and the client name in the title', () => {
  const patchData = { overall: { current: 91.5 }, clients: [{ key: 'client:3', label: 'Acme' }], types: [
    { key: 'windows-security', label: 'Windows Security', categories: [{ category: 'Security Updates', installed: 90, missing: 10, compliance: 90 }] },
    { key: 'third-party', label: 'Third Party', categories: [{ category: 'Apps', installed: 5, missing: 5, compliance: 50 }] }] };
  const all = extractReport(config([{ type: 'patches', filters: { patchScope: 'client:3' } }]), { b1: patchData }, period).sections[0];
  assert.equal(all.title, 'Patch Compliance — Acme');
  assert.equal(kpi(all, 'Overall score'), '91.5%');
  assert.equal(kpi(all, 'Patch entries'), 110);
  const one = extractReport(config([{ type: 'patches', filters: { patchType: 'third-party' } }]), { b1: patchData }, period).sections[0];
  assert.equal(one.items[0].kpis.some((k) => k.label === 'Overall score'), false);
  assert.equal(kpi(one, 'Missing patches'), 5);
});

test('patch details use the device name and carry the inventory only in the annex', () => {
  const details = [{ device: 'PC1', device_id: 7, title: 'KB1', kb: 'KB1', is_missing: true, patch_type: 'Windows Security' }, { device_id: 8, title: 'KB2', kb: null, is_missing: false, patch_type: 'Third Party' }];
  const { sections, annex } = extractReport(config([{ type: 'patch-details' }], { annex: true }), { b1: { patches: { types: [] }, details } }, period);
  assert.deepEqual(sections[0].items[0].data, [{ label: 'Windows Security', value: 1 }, { label: 'Third Party', value: 1 }]);
  assert.deepEqual(annex[0].items[0].rows.map((r) => [r[0], r[3]]), [['PC1', 'Yes'], [8, 'No']]);   // the device ID when there is no name
});

test('backups: worst first in the annex, labels readable, and the different empty states explained', () => {
  const rows = [
    { customer: 'Acme', device_name: 'A', health: 'OK', red_flag_count: 0, flags: [], device_type: 'Server', days_since_success: 1 },
    { customer: 'Acme', device_name: 'B', health: 'CRITICAL', red_flag_count: 2, flags: [{ category: 'backup_currency', flag: 'NO SUCCESSFUL BACKUP IN 12 DAYS' }, { category: null, flag: 'Exchange: Failed' }], device_type: 'Microsoft 365', days_since_success: 12 },
    { customer: 'Acme', device_name: 'C', health: 'WARNING', red_flag_count: 1, flags: [{ category: 'retention', flag: 'RETENTION SHORT' }], device_type: 'Workstation', days_since_success: 3 }];
  const result = extractReport(config([{ type: 'backups' }], { annex: true }), { b1: { available: true, loaded: true, rows } }, period);
  assert.deepEqual(result.annex[0].items[0].rows.map((r) => [r[1], r[2]]), [['B', 'Critical'], ['C', 'Warning'], ['A', 'OK']]);
  assert.equal(result.annex[0].items[0].rows[0][4], 'NO SUCCESSFUL BACKUP IN 12 DAYS; Exchange: Failed');
  const flagChart = result.sections[0].items.find((i) => i.title === 'Red Flags by Category');
  assert.deepEqual(flagChart.data.map((d) => d.label).sort(), ['Backup Currency', 'Retention', 'Session Failure']);
  const warn = (data) => extractReport(config([{ type: 'backups' }]), { b1: data }, period).warnings[0];
  assert.equal(warn({ available: false, rows: [] }), 'Backup Health: the Cove schema was not found.');
  assert.equal(warn({ available: true, loaded: false, rows: [] }), 'Backup Health: no backup data has been collected yet.');
  assert.equal(warn({ available: true, loaded: true, rows: [] }), 'Backup Health: no matching devices.');
});

test('countBy groups, labels blanks and sorts largest first', () => {
  assert.deepEqual(countBy([{ a: 'x' }, { a: 'y' }, { a: 'x' }, { a: '' }, {}], 'a'), [{ label: 'x', value: 2 }, { label: 'Unclassified', value: 2 }, { label: 'y', value: 1 }]);
});

test('every block type extracts without throwing when its data is missing', () => {
  for (const type of ['tickets', 'patches', 'patch-details', 'server-health', 'backups']) {
    const result = extractReport({ ...config([newBlock(type, 'b1')]) }, {}, period);
    assert.deepEqual(result.sections, [], type);
    assert.equal(result.warnings.length, 1, type);
  }
});

test('a filter with several values keeps the rows that have any of them, ignoring case', () => {
  const rows = { tickets: { rows: [ticket(1, 'Acme', 'High'), ticket(2, 'Zed', 'Low'), ticket(3, 'Beta', 'High'), ticket(4, 'Acme', 'Low')] } };
  const cfg = config([{ type: 'tickets', filters: { customer: ['acme', 'ZED'], priority: ['High', 'Low'] } }]);
  const { sections } = extractReport(cfg, { b1: rows }, period);
  assert.equal(kpi(sections[0], 'Ticket Count'), 3);                                  // Acme-High, Zed-Low, Acme-Low
  assert.match(sections[0].notes[1], /Customer: acme, ZED \| Priority: High, Low/);
});

test('two filters are both applied, so each narrows the other', () => {
  const rows = { tickets: { rows: [ticket(1, 'Acme', 'High'), ticket(2, 'Zed', 'Low'), ticket(3, 'Acme', 'Low')] } };
  const cfg = config([{ type: 'tickets', filters: { customer: ['Acme', 'Zed'], priority: 'Low' } }]);
  assert.equal(kpi(extractReport(cfg, { b1: rows }, period).sections[0], 'Ticket Count'), 2);
});

test('a heading names the values, and shortens a long list', () => {
  assert.equal(describeFilter(''), '');
  assert.equal(describeFilter('Acme'), 'Acme');
  assert.equal(describeFilter(['Acme', 'Zed']), 'Acme, Zed');
  assert.equal(describeFilter(['Acme', 'Zed', 'Beta', 'Cora']), 'Acme, Zed +2 more');
  assert.equal(blockTitle({ type: 'tickets', title: '', filters: { customer: ['Acme', 'Zed'] } }), 'Client Tickets — Acme, Zed');
});

test('a patch type filter with several types keeps the categories of each of them, and drops the overall score', () => {
  const patchData = { overall: { current: 90 }, types: [
    { key: 'windows-security', label: 'Windows Security', categories: [{ category: 'A', installed: 9, missing: 1, compliance: 90 }] },
    { key: 'windows-patching', label: 'Windows Patching', categories: [{ category: 'B', installed: 5, missing: 5, compliance: 50 }] },
    { key: 'third-party', label: 'Third Party', categories: [{ category: 'C', installed: 1, missing: 1, compliance: 50 }] }] };
  const both = extractReport(config([{ type: 'patches', filters: { patchType: ['windows-security', 'third-party'] } }]), { b1: patchData }, period).sections[0];
  assert.equal(kpi(both, 'Patch entries'), 12);                                       // categories A and C, not B
  assert.equal(both.items[0].kpis.some((k) => k.label === 'Overall score'), false);
});
