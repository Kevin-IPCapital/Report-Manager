import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { TILE_FILTERS, applyFilters, connectionOf, ncentralState, ncentralStatus, tileCounts, toggleFilter } from './serverHealth.js';

const real = JSON.parse(readFileSync(new URL('./report/fixtures/api-responses.json', import.meta.url), 'utf8'))['server-health'].servers;   // what the API really returns
const agent = (name, extra = {}) => ({ computer_name: name, machine_type: 'workstation', network_status: 'connected', infected: false, is_up_to_date: true, ...extra });
const fleet = [
  agent('SRV-FILE-01', { machine_type: 'server' }),
  agent('SRV-DB-01', { machine_type: 'server', network_status: 'disconnected', is_up_to_date: false }),
  agent('PC-RECEPTION', { infected: true }),
  agent('PC-SALES', { network_status: 'connecting', is_up_to_date: null, infected: null }),
  agent('PC-ODD', { network_status: 'who knows' }),
];

test('network status is read loosely: disconnected wins over connect, and an unfamiliar value is unknown', () => {
  assert.deepEqual(['connected', 'disconnected', 'Disconnecting', 'offline', 'online', 'connecting', 'who knows', null, ''].map(connectionOf),
    ['connected', 'disconnected', 'disconnected', 'disconnected', 'connected', 'connected', 'unknown', 'unknown', 'unknown']);
});

test('the tiles count what they say, and unknown values are not counted as bad', () => {
  assert.deepEqual(tileCounts(fleet), { endpoints: 5, servers: 2, connected: 3, disconnected: 1, ncConnected: 0, ncDisconnected: 0, infected: 1, outdated: 1 });   // PC-SALES has null flags: neither infected nor out of date; none of these has any N-central information
});

test('a tile filter keeps only what it names, and several filters all apply', () => {
  assert.deepEqual(applyFilters(fleet, { servers: true }).map((s) => s.computer_name), ['SRV-FILE-01', 'SRV-DB-01']);
  assert.deepEqual(applyFilters(fleet, { servers: true, connected: true }).map((s) => s.computer_name), ['SRV-FILE-01']);
  assert.deepEqual(applyFilters(fleet, { outdated: true }).map((s) => s.computer_name), ['SRV-DB-01']);
  assert.equal(applyFilters(fleet, {}).length, 5);
  assert.equal(applyFilters(fleet, { infected: false }).length, 5);   // a filter set to off is not a filter
});

test('connected and disconnected cannot both be on; turning one on turns the other off, and a second click turns it off', () => {
  let active = toggleFilter({}, 'connected');
  assert.deepEqual(active, { connected: true });
  active = toggleFilter(active, 'disconnected');
  assert.deepEqual(active, { disconnected: true });
  assert.deepEqual(toggleFilter(active, 'disconnected'), {});
  assert.deepEqual(toggleFilter({ servers: true }, 'infected'), { servers: true, infected: true });   // independent filters stack
});

test('the device filter matches parts of a name, case-insensitively, together with a tile filter', () => {
  assert.deepEqual(applyFilters(fleet, {}, 'srv').map((s) => s.computer_name), ['SRV-FILE-01', 'SRV-DB-01']);
  assert.deepEqual(applyFilters(fleet, {}, 'srv file').map((s) => s.computer_name), ['SRV-FILE-01']);
  assert.deepEqual(applyFilters(fleet, { servers: true }, 'db').map((s) => s.computer_name), ['SRV-DB-01']);
  assert.deepEqual(applyFilters(fleet, { infected: true }, 'srv'), []);
});

test('the real API rows work with every filter and tile', () => {
  assert.ok(real.length > 0);
  const counts = tileCounts(real);
  assert.equal(counts.endpoints, real.length);
  assert.equal(counts.servers, real.filter((s) => s.machine_type === 'server').length);
  assert.equal(applyFilters(real, { servers: true }, real[0].computer_name.slice(0, 3)).every((s) => s.machine_type === 'server'), true);
});

// ---- patching on a card
import { CARD_SECTIONS, collectedView, collectionView, daysAgo, patchView } from './serverHealth.js';
const category = (extra) => ({ key: 'windows-security', label: 'Windows Security Patching', last_installed_at: '2026-11-08T10:00:00Z', days_since_last: 3.4, next_due: null, up_to_date: true,
  missing: 0, oldest_missing_days: null, overdue: false, ...extra });
const matched = (...categories) => ({ status: 'matched', device_name: 'srv-a.acme.local', categories });

test('how long ago, in whole days', () => {
  assert.deepEqual([null, 0.2, 1, 1.9, 3.4, 40].map(daysAgo), ['', 'today', '1 day ago', '1 day ago', '3 days ago', '40 days ago']);
});

test('a card shows, for each category, when a patch was last added, the next due, and whether it is up to date', () => {
  const view = patchView(matched(category()));
  assert.equal(view.matched, true);
  assert.equal(view.device, 'srv-a.acme.local');
  const row = view.rows[0];
  assert.match(row.last, /2026.*\(3 days ago\)/);
  assert.equal(row.next, 'Not collected');
  assert.equal(row.upToDate, true);
  assert.equal(row.upTitle, 'Nothing is missing in this category.');
  assert.deepEqual([row.flag, row.flagText], ['', '']);
});

test('next due is shown as a date when there is one, and as not collected when there is not', () => {
  assert.match(patchView(matched(category({ next_due: '2026-11-20T00:00:00Z' }))).rows[0].next, /2026/);
  assert.equal(patchView(matched(category({ next_due: null }))).rows[0].next, 'Not collected');
  assert.match(patchView(matched(category())).rows[0].nextTitle, /schedule is not collected/);
});

test('up to date is a tick, not a tick, or unknown, and the three are never confused', () => {
  assert.equal(patchView(matched(category({ up_to_date: true }))).rows[0].upToDate, true);
  assert.equal(patchView(matched(category({ up_to_date: false, missing: 2 }))).rows[0].upToDate, false);
  assert.equal(patchView(matched(category({ up_to_date: null }))).rows[0].upToDate, null);
  assert.equal(patchView(matched(category({ up_to_date: undefined }))).rows[0].upToDate, null);
  assert.match(patchView(matched(category({ up_to_date: null }))).rows[0].upTitle, /nothing can be said/);
});

test('a category with something missing is not up to date, and says how many and for how long', () => {
  const row = patchView(matched(category({ up_to_date: false, missing: 2, oldest_missing_days: 20.6, overdue: true })), 14).rows[0];
  assert.deepEqual([row.upToDate, row.flag, row.flagText], [false, 'overdue', 'Overdue']);
  assert.match(row.upTitle, /2 patches are missing, the oldest for 20 days: more than the 14 days allowed/);
});

test('something missing for less than the grace period is flagged as missing, not overdue', () => {
  const row = patchView(matched(category({ up_to_date: false, missing: 1, oldest_missing_days: 5 })), 14).rows[0];
  assert.deepEqual([row.upToDate, row.flag, row.flagText], [false, 'missing', '1 missing']);
  assert.match(row.upTitle, /1 patch is missing, the oldest for 5 days, which is within the 14 days allowed/);
});

test('something missing with no known age still says so, without making up an age', () => {
  assert.equal(patchView(matched(category({ up_to_date: false, missing: 3, oldest_missing_days: null }))).rows[0].upTitle, '3 patches are missing.');
});

test('a category with no dated install says so instead of inventing a date', () => {
  assert.equal(patchView(matched(category({ last_installed_at: null, days_since_last: null }))).rows[0].last, 'No dated install');
});

test('there is no overall verdict on a server: only the three categories are shown', () => {
  const view = patchView(matched(category(), category({ key: 'windows-patching' }), category({ key: 'third-party' })));
  assert.equal(view.rows.length, 3);
  assert.deepEqual(Object.keys(view).sort(), ['collection', 'device', 'matched', 'rows']);      // the three rows, and how the collection went; no verdict of any kind
  assert.ok(!Object.keys(view).some((k) => /health|verdict|score|overall/i.test(k)));
});

test('a server with no patch data says why, in words', () => {
  assert.match(patchView({ status: 'no-match' }).note, /No N-central device with this name/);
  assert.match(patchView({ status: 'ambiguous' }).note, /More than one/);
  assert.match(patchView({ status: 'unlinked' }).note, /not linked to a client/);
  assert.match(patchView({ status: 'unavailable' }).note, /could not be read/);
  assert.equal(patchView({ status: 'no-match' }).matched, false);
  assert.match(patchView(undefined).note, /could not be read/);                       // an older answer with no patching at all
});

// ---- a device N-central cannot find
const matchedWith = (collection, inventory) => ({ status: 'matched', device_name: 'srv-b', categories: [], collection, inventory });

test('a device whose patch collection answered 404 is disconnected, and the note gives the date of its last successful inventory', () => {
  const view = collectionView(matchedWith({ state: 'disconnected', attempted_at: '2026-11-11T08:00:00Z', days_ago: 0.5 }, { at: '2026-11-08T08:00:00Z', days_ago: 3.1 }));
  assert.deepEqual([view.state, view.label, view.tone], ['disconnected', 'Disconnected', 'bad']);
  assert.match(view.note, /N-central cannot find this device/);
  assert.match(view.note, /answered 404, so it counts as disconnected/);
  assert.match(view.note, /last successful inventory, .*2026.*\(3 days ago\)/);
});

test('a disconnected device that never had a successful inventory says so rather than inventing a date', () => {
  const view = collectionView(matchedWith({ state: 'disconnected', attempted_at: '2026-11-11T08:00:00Z' }, null));
  assert.match(view.note, /No successful inventory has been recorded for it\./);
});

test('any other failure is a collection failure, not a disconnection', () => {
  const view = collectionView(matchedWith({ state: 'failed', attempted_at: '2026-11-11T08:00:00Z' }, { at: '2026-11-08T08:00:00Z', days_ago: 3 }));
  assert.deepEqual([view.state, view.label, view.tone], ['failed', 'Collection failed', 'warn']);
  assert.doesNotMatch(view.note, /disconnected/);
});

test('a device whose collection worked has no flag and no note, and one never tried says so', () => {
  assert.deepEqual(collectionView(matchedWith({ state: 'ok' }, null)), { state: 'ok', label: '', tone: '', note: '' });
  assert.match(collectionView(matchedWith({ state: null }, null)).note, /No patch collection has been recorded/);
  assert.equal(collectionView(matchedWith({ state: null }, null)).label, '');
  assert.equal(collectionView(undefined).state, null);                                  // an older answer with no collection information
});

test('the patch view carries the collection status along with the table rows', () => {
  const view = patchView({ status: 'matched', device_name: 'x', categories: [category()], collection: { state: 'disconnected', attempted_at: '2026-11-11T00:00:00Z' }, inventory: { at: '2026-11-08T00:00:00Z', days_ago: 3 } });
  assert.equal(view.collection.label, 'Disconnected');
  assert.equal(view.rows.length, 1);
});

// ---- the three sections of a card
test('a card has three sections in a fixed order, named as the owner asked', () => {
  assert.deepEqual(CARD_SECTIONS.map((s) => s.title), ['Performance', 'Patches', 'Data Collected']);
  assert.deepEqual(CARD_SECTIONS.map((s) => s.key), ['performance', 'patches', 'collected']);
});

// ---- Data Collected
const item = (key, label, what, state, at = null, days_ago = null) => ({ key, label, what, state, at, days_ago });

test('data collected shows, for each collector, the latest date found and how long ago', () => {
  const rows = collectedView([item('sentinelone', 'SentinelOne', 'Security agent', 'found', '2026-11-10T12:00:00Z', 1.0), item('cove', 'Cove', 'Backup', 'found', '2026-11-09T06:00:00Z', 2.5)]);
  assert.deepEqual(rows.map((r) => [r.source, r.what, r.found]), [['SentinelOne', 'Security agent', true], ['Cove', 'Backup', true]]);
  assert.match(rows[0].text, /2026.*\(1 day ago\)/);
  assert.match(rows[1].text, /2026.*\(2 days ago\)/);
  assert.match(rows[0].title, /^Captured /);
});

test('a collector with nothing for this device says why in words, and never shows a date', () => {
  const rows = collectedView([
    item('a', 'N-central', 'Device inventory', 'none'), item('b', 'N-central', 'Patch inventory', 'no-match'), item('c', 'Cove', 'Backup', 'ambiguous'),
    item('d', 'Cove', 'Backup', 'unlinked'), item('e', 'Cove', 'Backup', 'not-collected'), item('f', 'Cove', 'Backup', 'unavailable'),
  ]);
  assert.deepEqual(rows.map((r) => r.text), ['No date recorded', 'No matching device in this client', 'More than one device has this name', 'This server is not linked to a client', 'Nothing collected yet', 'Could not be read just now']);
  assert.ok(rows.every((r) => !r.found && r.title === ''));
});

test('an unknown state is treated as not readable, and no data at all is an empty table', () => {
  assert.equal(collectedView([item('x', 'X', 'Y', 'something-new')])[0].text, 'Could not be read just now');
  assert.deepEqual(collectedView(undefined), []);
  assert.deepEqual(collectedView([]), []);
});

test('a date and how long ago are also given on their own, so a narrow card can put them on two lines', () => {
  const row = patchView(matched(category())).rows[0];
  assert.match(row.lastDate, /2026/);
  assert.equal(row.lastAgo, '3 days ago');
  assert.ok(row.last.startsWith(row.lastDate) && row.last.includes(row.lastAgo));       // the one-line form is built from the same two parts
  const none = patchView(matched(category({ last_installed_at: null, days_since_last: null }))).rows[0];
  assert.deepEqual([none.lastDate, none.lastAgo], ['No dated install', '']);
  const collected = collectedView([item('s', 'SentinelOne', 'Security agent', 'found', '2026-11-10T12:00:00Z', 1.0), item('c', 'Cove', 'Backup', 'no-match')]);
  assert.deepEqual([collected[0].ago, collected[1].date, collected[1].ago], ['1 day ago', 'No matching device in this client', '']);
  assert.match(collected[0].date, /2026/);
});

// ---- two connection statuses: SentinelOne's, and N-central's (can it find the device?)
const withNc = (name, status, state, extra = {}) => agent(name, { patches: status === 'matched' ? { status, collection: { state }, categories: [] } : { status }, ...extra });
const mixed = [
  withNc('SRV-A', 'matched', 'ok'),
  withNc('SRV-B', 'matched', 'disconnected', { network_status: 'disconnected' }),
  withNc('SRV-C', 'matched', 'failed'),
  withNc('SRV-D', 'matched', null),
  withNc('SRV-E', 'no-match', null),
  agent('SRV-F'),                                                                   // an older answer with no patching at all
];

test('the N-central state is only read from a matched server, and anything else has none', () => {
  assert.deepEqual(mixed.map(ncentralState), ['ok', 'disconnected', 'failed', null, null, null]);
});

test('the N-central tiles count servers by whether N-central can find them, and a server it cannot match is in neither', () => {
  const counts = tileCounts(mixed);
  assert.deepEqual([counts.ncConnected, counts.ncDisconnected], [1, 1]);
  assert.equal(counts.endpoints, 6);                                                // failed, never collected and unmatched servers are counted as neither connected nor disconnected
});

test('the SentinelOne tiles and the N-central tiles are independent: a server can be connected to one and disconnected from the other', () => {
  const sentinelOnly = applyFilters(mixed, { connected: true }).map((s) => s.computer_name);
  const ncOnly = applyFilters(mixed, { ncDisconnected: true }).map((s) => s.computer_name);
  assert.deepEqual(sentinelOnly, ['SRV-A', 'SRV-C', 'SRV-D', 'SRV-E', 'SRV-F']);
  assert.deepEqual(ncOnly, ['SRV-B']);
  assert.deepEqual(applyFilters(mixed, { disconnected: true, ncDisconnected: true }).map((s) => s.computer_name), ['SRV-B']);
  assert.deepEqual(applyFilters(mixed, { connected: true, ncDisconnected: true }), []);          // connected to SentinelOne and not found by N-central: none here
});

test('each pair of connection tiles is contradictory only within itself', () => {
  let active = toggleFilter({}, 'ncConnected');
  active = toggleFilter(active, 'ncDisconnected');
  assert.deepEqual(active, { ncDisconnected: true });                               // N-central connected turned off
  active = toggleFilter(active, 'connected');
  assert.deepEqual(active, { ncDisconnected: true, connected: true });              // the SentinelOne pair does not touch the N-central pair
  active = toggleFilter(active, 'disconnected');
  assert.deepEqual(active, { ncDisconnected: true, disconnected: true });
  assert.deepEqual(toggleFilter(active, 'ncDisconnected'), { disconnected: true });
});

test('the tile labels say whose connection they count, and the remembered keys of the first two are unchanged', () => {
  assert.deepEqual([TILE_FILTERS.connected.label, TILE_FILTERS.disconnected.label, TILE_FILTERS.ncConnected.label, TILE_FILTERS.ncDisconnected.label],
    ['SentinelOne Connected', 'SentinelOne Disconnected', 'N-central Connected', 'N-central Disconnected']);
  assert.ok(Object.keys(TILE_FILTERS).includes('connected') && Object.keys(TILE_FILTERS).includes('disconnected'));   // what people already have remembered still means the same
});

test('the N-central status says connected, disconnected, a failed collection, not collected or unknown, each with a reason', () => {
  const status = (state, extra = {}) => ncentralStatus({ status: 'matched', collection: { state, attempted_at: '2026-11-11T08:00:00Z' }, inventory: { at: '2026-11-08T08:00:00Z', days_ago: 3 }, categories: [], ...extra });
  assert.deepEqual([status('ok').key, status('ok').label, status('ok').tone], ['connected', 'Connected', 'good']);
  assert.deepEqual([status('disconnected').key, status('disconnected').label, status('disconnected').tone], ['disconnected', 'Disconnected', 'bad']);
  assert.match(status('disconnected').title, /answered 404/);
  assert.match(status('disconnected').title, /last successful inventory/);
  assert.deepEqual([status('failed').key, status('failed').label, status('failed').tone], ['failed', 'Collection failed', 'warn']);
  assert.deepEqual([status(null).key, status(null).label], ['never', 'Not collected']);
  assert.match(status(null).title, /No patch collection has been recorded/);
});

test('with no matching N-central device the status is unknown and says why, and an old answer with no patching is unknown too', () => {
  for (const [status, words] of [['no-match', /No N-central device with this name/], ['ambiguous', /More than one/], ['unlinked', /not linked to a client/], ['unavailable', /could not be read/]]) {
    const view = ncentralStatus({ status });
    assert.deepEqual([view.key, view.label, view.tone], ['unknown', 'Unknown', '']);
    assert.match(view.title, words);
  }
  assert.equal(ncentralStatus(undefined).key, 'unknown');
});
