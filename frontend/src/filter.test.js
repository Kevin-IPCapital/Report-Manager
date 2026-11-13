import assert from 'node:assert/strict';
import { test } from 'node:test';
import { columnKind, comparison, matchesDevice, matchesQuery, searchHint } from './filter.js';

const num = { key: 'n' };
const hours = { key: 'h', type: 'duration' };
const pct = { key: 'p', type: 'percent' };
const when = { key: 'd', type: 'datetime' };
const size = { key: 's', type: 'bytes' };
const GB = 1024 ** 3;
const ok = (q, col, kind, value) => comparison(q, col, kind)?.(value);
const local = (y, m, d, h = 12, min = 0) => new Date(y, m - 1, d, h, min).toISOString(); // local time, so these hold in any time zone

test('numbers: each operator, and a bare number means equals', () => {
  assert.deepEqual([5, 6, 4].map((v) => ok('>5', num, 'number', v)), [false, true, false]);
  assert.deepEqual([5, 6, 4].map((v) => ok('>=5', num, 'number', v)), [true, true, false]);
  assert.deepEqual([5, 6, 4].map((v) => ok('<5', num, 'number', v)), [false, false, true]);
  assert.deepEqual([5, 6, 4].map((v) => ok('<=5', num, 'number', v)), [true, false, true]);
  assert.deepEqual([5, 6].map((v) => ok('=5', num, 'number', v)), [true, false]);
  assert.deepEqual([5, 6].map((v) => ok('5', num, 'number', v)), [true, false]);
  assert.deepEqual([5, 6].map((v) => ok('!=5', num, 'number', v)), [false, true]);
});

test('numbers: spaces, unicode operators, ranges and "and"', () => {
  assert.equal(ok('> 5', num, 'number', 6), true);
  assert.equal(ok('≥ 5', num, 'number', 5), true);
  assert.equal(ok('≤5', num, 'number', 6), false);
  assert.equal(ok('<>5', num, 'number', 6), true);
  assert.deepEqual([4, 5, 10, 11].map((v) => ok('5..10', num, 'number', v)), [false, true, true, false]);
  assert.deepEqual([10, 5].map((v) => ok('10..5', num, 'number', v)), [true, true]);   // order does not matter
  assert.deepEqual([3, 5, 12].map((v) => ok('>2, <10', num, 'number', v)), [true, true, false]);
  assert.equal(ok('-3..3', num, 'number', 0), true);
});

test('numbers: empty values never match, and 0 is a real value', () => {
  assert.equal(ok('>0', num, 'number', null), false);
  assert.equal(ok('=0', num, 'number', 0), true);
  assert.equal(ok('<5', num, 'number', ''), false);
});

test('anything that is not a valid comparison is left to the text search', () => {
  for (const q of ['abc', '>abc', '5..', '..5', '1..2..3', '>', '']) assert.equal(comparison(q, num, 'number'), null, q);
  assert.equal(comparison('>5', num, 'text'), null);
  assert.equal(matchesQuery(num, 'number', 12, '12', 'abc'), false);
  assert.equal(matchesQuery({ key: 't' }, 'text', 'Server One', 'Server One', 'one'), true);
  assert.equal(matchesQuery({ key: 't' }, 'text', 'Server One', 'Server One', '>5'), false);   // text columns are never compared
});

test('durations: units, a bare number is hours, and the cell rounding is tolerated', () => {
  assert.deepEqual([1.9, 2.1].map((v) => ok('>2h', hours, 'number', v)), [false, true]);
  assert.deepEqual([0.4, 0.6].map((v) => ok('<=30m', hours, 'number', v)), [true, false]);
  assert.equal(ok('>2', hours, 'number', 2.5), true);
  assert.deepEqual([23, 30, 49].map((v) => ok('1d..2d', hours, 'number', v)), [false, true, false]);
  assert.equal(ok('=2h', hours, 'number', 2 + 20 / 3600), true);       // 2 h 20 s is shown as "2 hours, 1 min"
  assert.equal(ok('>2 hours', hours, 'number', 3), true);
  assert.equal(comparison('>2x', hours, 'number'), null);               // unknown unit
});

test('percentages accept a percent sign', () => {
  assert.deepEqual([79.9, 80, 100].map((v) => ok('80%..100%', pct, 'number', v)), [false, true, true]);
  assert.equal(ok('<90', pct, 'number', 89.5), true);
});

test('dates: a day is a whole day', () => {
  const morning = local(2026, 10, 5, 8), evening = local(2026, 10, 5, 23, 30), next = local(2026, 10, 6, 0, 30), before = local(2026, 10, 4, 23, 30);
  assert.deepEqual([morning, evening, next, before].map((v) => ok('=2026-10-05', when, 'date', v)), [true, true, false, false]);
  assert.deepEqual([morning, evening, next, before].map((v) => ok('>2026-10-05', when, 'date', v)), [false, false, true, false]);
  assert.deepEqual([morning, next, before].map((v) => ok('>=2026-10-05', when, 'date', v)), [true, true, false]);
  assert.deepEqual([morning, evening, next, before].map((v) => ok('<2026-10-05', when, 'date', v)), [false, false, false, true]);
  assert.deepEqual([evening, next].map((v) => ok('<=2026-10-05', when, 'date', v)), [true, false]);
  assert.deepEqual([morning, next].map((v) => ok('!=2026-10-05', when, 'date', v)), [false, true]);
});

test('dates: ranges include both end days, and invalid dates fall back to text', () => {
  assert.deepEqual([local(2026, 9, 30), local(2026, 10, 1, 0, 5), local(2026, 10, 31, 23, 30), local(2026, 11, 1, 0, 5)]
    .map((v) => ok('2026-10-01..2026-10-31', when, 'date', v)), [false, true, true, false]);
  assert.equal(comparison('2026-02-30', when, 'date'), null);      // not a real day
  assert.equal(comparison('october', when, 'date'), null);
  assert.equal(ok('>2026-10-01', when, 'date', null), false);
  assert.equal(ok('>2026-10-01', when, 'date', 'not a date'), false);
});

test('column kind: declared types, and automatic detection of plain numbers', () => {
  const rows = [{ n: 1, t: 'a', m: 1, e: null, d: 'x' }, { n: 2, t: 'b', m: 'x', e: 3, d: null }];
  assert.equal(columnKind({ key: 'n' }, rows), 'number');
  assert.equal(columnKind({ key: 't' }, rows), 'text');
  assert.equal(columnKind({ key: 'm' }, rows), 'text');           // mixed values are text
  assert.equal(columnKind({ key: 'e' }, rows), 'number');         // empty values are ignored
  assert.equal(columnKind({ key: 'z' }, []), 'text');             // nothing to judge by
  assert.equal(columnKind({ key: 'x', type: 'duration' }, []), 'number');
  assert.equal(columnKind({ key: 'x', type: 'percent' }, []), 'number');
  assert.equal(columnKind({ key: 'x', type: 'datetime' }, []), 'date');
  for (const type of ['status', 'bool', 'flag', 'priority', 'link']) assert.equal(columnKind({ key: 'n', type }, rows), 'text', type);
});

test('hints say what the column understands', () => {
  assert.match(searchHint('number', hours, 'Response Time'), />2h/);
  assert.match(searchHint('number', pct, 'Compliance'), /80\.\.100/);
  assert.match(searchHint('number', num, 'Total'), />5/);
  assert.match(searchHint('date', when, 'Created'), /2026-10-01\.\./);
  assert.equal(searchHint('text', { key: 't' }, 'Title'), 'Search Title');
});

test('sizes: units, a bare number is GB, and the cell rounding is tolerated', () => {
  assert.deepEqual([99 * GB, 101 * GB].map((v) => ok('>100gb', size, 'number', v)), [false, true]);
  assert.deepEqual([0.9 * 1024 ** 4, 1.1 * 1024 ** 4].map((v) => ok('<=1tb', size, 'number', v)), [true, false]);
  assert.deepEqual([400 * 1024 ** 2, 1 * GB, 3 * GB].map((v) => ok('500mb..2gb', size, 'number', v)), [false, true, false]);
  assert.equal(ok('>5', size, 'number', 6 * GB), true);                       // bare number: GB
  assert.equal(ok('=3gb', size, 'number', 3 * GB + 2 * 1024 ** 2), true);     // 3.00 GB is shown for 3 GB and 2 MB
  assert.equal(ok('>1', size, 'number', null), false);
  assert.equal(comparison('>2x', size, 'number'), null);                       // unknown unit
  assert.match(searchHint('number', size, 'Used Storage'), />100gb/);
  assert.equal(columnKind({ key: 'x', type: 'bytes' }, []), 'number');
});

test('a device filter needs every word typed to appear in the name, in any order, ignoring case', () => {
  assert.equal(matchesDevice('srv', 'SRV-FILE-01'), true);
  assert.equal(matchesDevice('file srv', 'SRV-FILE-01'), true);
  assert.equal(matchesDevice('  SRV   01 ', 'srv-file-01'), true);
  assert.equal(matchesDevice('srv db', 'SRV-FILE-01'), false);
  assert.equal(matchesDevice('pc', 'SRV-FILE-01'), false);
});

test('nothing typed matches every device, and the words may be in any of several names', () => {
  for (const q of ['', '   ', null, undefined]) assert.equal(matchesDevice(q, 'anything'), true);
  assert.equal(matchesDevice('', null), true);
  assert.equal(matchesDevice('mailbox', 'Device 7', 'm365-mailboxes'), true);      // the device name or the hostname
  assert.equal(matchesDevice('mailbox', null, undefined), false);                  // nothing to match against
});

// ---- client and device filters
import { clientChoices, clientKey, matchesClients, matchesDevices } from './filter.js';

test('a client is told apart by its ID when it has one, and by its customer name when it has not', () => {
  assert.equal(clientKey(7, 'Acme'), '7');
  assert.equal(clientKey(null, 'Zed'), 'name:Zed');
  assert.equal(clientKey(undefined, undefined), 'name:');
  assert.equal(clientKey(0, 'Zero'), '0');                                           // an ID of 0 is still an ID
});

test('the choices are the clients on the page, once each, by name', () => {
  const rows = [{ id: 2, n: 'Zed' }, { id: 1, n: 'acme' }, { id: 2, n: 'Zed (other spelling)' }, { id: null, n: 'Loose Co' }, { id: null, n: '' }];
  assert.deepEqual(clientChoices(rows, (r) => r.id, (r) => r.n), [{ value: '1', label: 'acme' }, { value: 'name:Loose Co', label: 'Loose Co' }, { value: 'name:', label: 'No customer' }, { value: '2', label: 'Zed' }]);
  assert.deepEqual(clientChoices([], () => 1, () => 'x'), []);
});

test('nothing chosen shows every client, and otherwise only the chosen ones', () => {
  assert.equal(matchesClients([], 5, 'A'), true);
  assert.equal(matchesClients(['5', '9'], 5, 'A'), true);
  assert.equal(matchesClients(['5'], 6, 'B'), false);
  assert.equal(matchesClients(['name:Loose Co'], null, 'Loose Co'), true);
  assert.equal(matchesClients(['name:Loose Co'], null, 'Other'), false);
  assert.equal(matchesClients(['5'], null, 'Acme'), false);                                 // an unlinked customer is not client 5 because it is called like it
});

test('several devices at once: a row matches if it matches any of them, each as a typed name would', () => {
  assert.equal(matchesDevices([], 'SRV-A'), true);
  assert.equal(matchesDevices(['srv-a', 'pc-1'], 'SRV-A'), true);
  assert.equal(matchesDevices(['srv-a', 'pc-1'], 'PC-1'), true);
  assert.equal(matchesDevices(['srv-a', 'pc-1'], 'SRV-B'), false);
  assert.equal(matchesDevices(['srv file'], 'SRV-FILE-01'), true);                           // words, as before
  assert.equal(matchesDevices(['file'], 'SRV-X', 'file-host'), true);                        // any of the names a row has (name or host name)
  assert.equal(matchesDevices(['zzz'], null, undefined), false);
});
