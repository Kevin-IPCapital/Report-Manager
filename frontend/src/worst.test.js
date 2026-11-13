import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { SERVER_SORT_KEYS, compareTickets, serverRank, sortServers, worstBackups, worstCategories, worstTickets } from './worst.js';

const real = JSON.parse(readFileSync(new URL('./report/fixtures/api-responses.json', import.meta.url), 'utf8'));
const names = (rows, key) => rows.map((r) => r[key]);

// ---- tickets
const t = (n, extra = {}) => ({ ticket_number: n, created_at: '2026-10-10T08:00:00Z', completed_at: null, rowStatus: null, sla_met: null, resolution_hours: null, ...extra });

test('tickets: late or flagged first, then open before closed, the longest open first', () => {
  const rows = [
    t('closed-fast', { completed_at: '2026-10-11T08:00:00Z', resolution_hours: 2 }),
    t('open-new', { created_at: '2026-10-20T08:00:00Z' }),
    t('open-old', { created_at: '2026-10-01T08:00:00Z' }),
    t('flagged-new', { rowStatus: 'bad', created_at: '2026-10-25T08:00:00Z' }),
    t('sla-missed-closed', { sla_met: false, completed_at: '2026-10-12T08:00:00Z', resolution_hours: 30 }),
    t('closed-slow', { completed_at: '2026-10-15T08:00:00Z', resolution_hours: 90 }),
  ];
  assert.deepEqual(names(worstTickets(rows), 'ticket_number'), ['flagged-new', 'sla-missed-closed', 'open-old', 'open-new', 'closed-slow', 'closed-fast']);
});

test('tickets: inside the late group the same rules apply', () => {
  const rows = [t('late-closed', { sla_met: false, completed_at: 'x', resolution_hours: 5 }), t('late-open-b', { rowStatus: 'bad', created_at: '2026-10-05T00:00:00Z' }), t('late-open-a', { sla_met: false, created_at: '2026-10-02T00:00:00Z' })];
  assert.deepEqual(names(worstTickets(rows), 'ticket_number'), ['late-open-a', 'late-open-b', 'late-closed']);
});

test('tickets: missing dates and resolution times sort last, ties go by ticket number, and the input is left alone', () => {
  const rows = [t('T2', { created_at: null }), t('T3', { created_at: '2026-10-01T00:00:00Z' }), t('T1', { created_at: '2026-10-01T00:00:00Z' }),
    t('C2', { completed_at: 'x', resolution_hours: null }), t('C1', { completed_at: 'x', resolution_hours: 4 })];
  const before = JSON.stringify(rows);
  assert.deepEqual(names(worstTickets(rows), 'ticket_number'), ['T1', 'T3', 'T2', 'C1', 'C2']);
  assert.equal(JSON.stringify(rows), before);
  assert.equal(compareTickets(rows[0], rows[0]), 0);
});

test('tickets: the real rows come out flagged first', () => {
  const rows = worstTickets(real.tickets.tickets.rows);
  const firstOk = rows.findIndex((r) => !r.rowStatus && r.sla_met !== false);
  assert.ok(rows.slice(0, firstOk).every((r) => r.rowStatus || r.sla_met === false));
  assert.ok(rows.slice(firstOk).every((r) => !r.rowStatus && r.sla_met !== false));
});

// ---- servers
const s = (name, extra = {}) => ({ computer_name: name, customer_name: 'Acme', network_status: 'connected', infected: false, data_age_hours: 1, ...extra });

test('servers: infected, then disconnected, then unknown, then connected; the oldest data first inside each', () => {
  const rows = [
    s('ok-new', { data_age_hours: 1 }), s('ok-old', { data_age_hours: 50 }), s('off', { network_status: 'disconnected', data_age_hours: 2 }),
    s('odd', { network_status: 'who knows' }), s('infected-but-connected', { infected: true }), s('off-old', { network_status: 'offline', data_age_hours: 90 }),
  ];
  assert.deepEqual(names(sortServers(rows), 'computer_name'), ['infected-but-connected', 'off-old', 'off', 'odd', 'ok-old', 'ok-new']);
  assert.deepEqual(rows.map(serverRank), [3, 3, 1, 2, 0, 1]);
});

test('servers: an unknown infected flag is not treated as infected, and missing ages go last', () => {
  const rows = [s('a', { infected: null, data_age_hours: null }), s('b', { infected: null, data_age_hours: 5 })];
  assert.deepEqual(names(sortServers(rows), 'computer_name'), ['b', 'a']);
});

test('servers: the other orders a person can choose', () => {
  const rows = [s('b-srv', { customer_name: 'Zed', data_age_hours: 5 }), s('a-srv', { customer_name: 'Zed', data_age_hours: 9 }), s('c-srv', { customer_name: 'Acme', data_age_hours: 1 })];
  assert.deepEqual(names(sortServers(rows, 'name'), 'computer_name'), ['a-srv', 'b-srv', 'c-srv']);
  assert.deepEqual(names(sortServers(rows, 'customer'), 'computer_name'), ['c-srv', 'a-srv', 'b-srv']);
  assert.deepEqual(names(sortServers(rows, 'age'), 'computer_name'), ['a-srv', 'b-srv', 'c-srv']);
  assert.deepEqual(names(sortServers(rows, 'nonsense'), 'computer_name'), names(sortServers(rows, 'worst'), 'computer_name'));   // an unknown key means worst first
  assert.deepEqual(SERVER_SORT_KEYS, ['worst', 'name', 'customer', 'age']);
});

test('servers: names sort without regard to case or accents and numbers sort as numbers', () => {
  const rows = [s('srv-10'), s('SRV-2'), s('srv-1')];
  assert.deepEqual(names(sortServers(rows, 'name'), 'computer_name'), ['srv-1', 'SRV-2', 'srv-10']);
});

test('servers: the real rows sort without error and keep every row', () => {
  const rows = sortServers(real['server-health'].servers);
  assert.equal(rows.length, real['server-health'].servers.length);
});

// ---- patch categories
test('patch categories: lowest compliance first, a category with no figure last, ties by missing patches then name', () => {
  const rows = [
    { category: 'Updates', compliance: 90, missing: 5 }, { category: 'Drivers', compliance: 40, missing: 2 }, { category: 'Empty', compliance: null, missing: 0 },
    { category: 'Tools', compliance: 40, missing: 9 }, { category: 'Apps', compliance: 90, missing: 5 },
  ];
  assert.deepEqual(names(worstCategories(rows), 'category'), ['Tools', 'Drivers', 'Apps', 'Updates', 'Empty']);
});

test('patch categories: the real ones sort by their compliance', () => {
  const categories = real.patches.types.flatMap((x) => x.categories);
  const sorted = worstCategories(categories).map((c) => c.compliance).filter((c) => c != null);
  assert.deepEqual(sorted, [...sorted].sort((a, b) => a - b));
});

// ---- backups
const b = (name, health, days, customer = 'Acme') => ({ device_name: name, health, days_since_success: days, customer });

test('backups: critical, warning, then OK; the longest since a good backup first, never-succeeded the longest of all', () => {
  const rows = [b('ok', 'OK', 1), b('warn-3', 'WARNING', 3), b('crit-2', 'CRITICAL', 2), b('crit-never', 'CRITICAL', null), b('crit-9', 'CRITICAL', 9), b('odd', 'MYSTERY', 1)];
  assert.deepEqual(names(worstBackups(rows), 'device_name'), ['crit-never', 'crit-9', 'crit-2', 'warn-3', 'ok', 'odd']);
});

test('backups: ties go by customer then device, and the real rows come out critical first', () => {
  assert.deepEqual(names(worstBackups([b('z', 'OK', 1, 'Beta'), b('a', 'OK', 1, 'Beta'), b('m', 'OK', 1, 'Alpha')]), 'device_name'), ['m', 'a', 'z']);
  const rows = worstBackups(real.backups.rows);
  assert.deepEqual(rows.map((r) => r.health), [...rows.map((r) => r.health)].sort((x, y) => ['CRITICAL', 'WARNING', 'OK'].indexOf(x) - ['CRITICAL', 'WARNING', 'OK'].indexOf(y)));
});

// ---- backups: red flags break a tie (owner, 12 Nov 2026)
const f = (name, health, days, flags, customer = 'Acme') => ({ ...b(name, health, days, customer), red_flag_count: flags });

test('backups: with the same health and the same days since a good backup, the device with more red flags is worse', () => {
  const rows = [f('one-flag', 'CRITICAL', 5, 1), f('five-flags', 'CRITICAL', 5, 5), f('no-flags', 'CRITICAL', 5, 0), f('three-flags', 'CRITICAL', 5, 3)];
  assert.deepEqual(names(worstBackups(rows), 'device_name'), ['five-flags', 'three-flags', 'one-flag', 'no-flags']);
});

test('backups: red flags never override health or the days since a good backup', () => {
  const rows = [f('many-flags-ok', 'OK', 1, 20), f('few-flags-critical', 'CRITICAL', 1, 0), f('flags-newer', 'WARNING', 2, 9), f('flags-older', 'WARNING', 8, 0)];
  assert.deepEqual(names(worstBackups(rows), 'device_name'), ['few-flags-critical', 'flags-older', 'flags-newer', 'many-flags-ok']);   // health first, then days, flags only after both
});

test('backups: a device with no red flag count is treated as having none, and equal flags fall back to customer then device', () => {
  const rows = [b('unknown', 'OK', 1), f('zero', 'OK', 1, 0), f('b-two', 'OK', 1, 2, 'Beta'), f('a-two', 'OK', 1, 2, 'Beta'), f('alpha-two', 'OK', 1, 2, 'Alpha')];
  assert.deepEqual(names(worstBackups(rows), 'device_name'), ['alpha-two', 'a-two', 'b-two', 'unknown', 'zero']);
});

test('backups: the tie breaker does not change a list that has no red flags anywhere', () => {
  const rows = [b('ok', 'OK', 1), b('crit', 'CRITICAL', 4), b('warn', 'WARNING', 2)];
  assert.deepEqual(names(worstBackups(rows), 'device_name'), ['crit', 'warn', 'ok']);
});
