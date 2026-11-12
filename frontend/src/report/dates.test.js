import assert from 'node:assert/strict';
import { test } from 'node:test';
import { describeDuration, durationRange, iso, presets, resolveDates } from './dates.js';

const day = (y, m, d) => new Date(y, m - 1, d);
const byKey = (today) => Object.fromEntries(presets(today).map((p) => [p.key, `${p.from}..${p.to}`]));

test('presets from the middle of a quarter', () => {
  assert.deepEqual(byKey(day(2026, 11, 10)), {
    'last-30': '2026-10-12..2026-11-10', 'this-month': '2026-11-01..2026-11-10', 'last-month': '2026-10-01..2026-10-31',
    'this-quarter': '2026-10-01..2026-11-10', 'last-quarter': '2026-07-01..2026-09-30',
  });
});

test('presets at the start of a year look back into the previous one', () => {
  assert.deepEqual(byKey(day(2027, 1, 15)), {
    'last-30': '2026-12-17..2027-01-15', 'this-month': '2027-01-01..2027-01-15', 'last-month': '2026-12-01..2026-12-31',
    'this-quarter': '2027-01-01..2027-01-15', 'last-quarter': '2026-10-01..2026-12-31',
  });
});

test('last month knows about leap years and month lengths', () => {
  assert.equal(byKey(day(2028, 3, 1))['last-month'], '2028-02-01..2028-02-29');
  assert.equal(byKey(day(2027, 3, 1))['last-month'], '2027-02-01..2027-02-28');
  assert.equal(byKey(day(2026, 5, 31))['last-month'], '2026-04-01..2026-04-30');
});

test('each quarter starts on the right month', () => {
  assert.equal(byKey(day(2026, 2, 5))['this-quarter'], '2026-01-01..2026-02-05');
  assert.equal(byKey(day(2026, 5, 5))['this-quarter'], '2026-04-01..2026-05-05');
  assert.equal(byKey(day(2026, 8, 5))['last-quarter'], '2026-04-01..2026-06-30');
});

test('iso pads and uses local dates', () => assert.equal(iso(day(2026, 3, 4)), '2026-03-04'));

test('entered dates win, then the report\'s own, then the last 30 days', () => {
  const today = day(2026, 11, 10);
  assert.deepEqual(resolveDates({ from: '2026-09-01', to: '2026-09-30' }, { from: '2026-01-01', to: '2026-01-31' }, today), { from: '2026-09-01', to: '2026-09-30' });
  assert.deepEqual(resolveDates({}, { from: '2026-01-01', to: '2026-01-31' }, today), { from: '2026-01-01', to: '2026-01-31' });
  assert.deepEqual(resolveDates({ from: '', to: '' }, { from: '', to: '' }, today), { from: '2026-10-12', to: '2026-11-10' });
  assert.deepEqual(resolveDates(undefined, undefined, today), { from: '2026-10-12', to: '2026-11-10' });
});

test('a start date alone runs up to today', () => {
  assert.deepEqual(resolveDates({ from: '2026-11-01' }, {}, day(2026, 11, 10)), { from: '2026-11-01', to: '2026-11-10' });
});

test('dates that cannot be used are refused', () => {
  assert.throws(() => resolveDates({ from: '2026-11-10', to: '2026-11-01' }, {}, day(2026, 11, 10)), /Start date/);
  assert.throws(() => resolveDates({ from: 'yesterday', to: '2026-11-01' }, {}, day(2026, 11, 10)), /YYYY-MM-DD/);
});

const range = (last, today) => { const r = durationRange(last, today); return `${r.from}..${r.to}`; };

test('the last N days counts today as the first of them', () => {
  assert.equal(range({ amount: 30, unit: 'days' }, day(2026, 11, 10)), '2026-10-12..2026-11-10');      // the same as the "Last 30 days" preset
  assert.equal(range({ amount: 1, unit: 'days' }, day(2026, 11, 10)), '2026-11-10..2026-11-10');
  assert.equal(range({ amount: 30, unit: 'days' }, day(2026, 11, 10)), byKey(day(2026, 11, 10))['last-30']);
});

test('weeks are seven days each', () => {
  assert.equal(range({ amount: 2, unit: 'weeks' }, day(2026, 11, 10)), '2026-10-28..2026-11-10');
  assert.equal(range({ amount: 1, unit: 'weeks' }, day(2026, 11, 10)), '2026-11-04..2026-11-10');
});

test('the last N months starts the day after the same date N months back, and copes with short months and year ends', () => {
  assert.equal(range({ amount: 3, unit: 'months' }, day(2026, 11, 10)), '2026-08-11..2026-11-10');
  assert.equal(range({ amount: 1, unit: 'months' }, day(2026, 3, 31)), '2026-03-01..2026-03-31');       // 31 Mar minus a month is 28 Feb
  assert.equal(range({ amount: 3, unit: 'months' }, day(2027, 1, 15)), '2026-10-16..2027-01-15');
  assert.equal(range({ amount: 12, unit: 'months' }, day(2028, 2, 29)), '2027-03-01..2028-02-29');       // across a leap day
});

test('a duration is worked out again on each day it runs, which is the point', () => {
  const last = { amount: 30, unit: 'days' };
  assert.notEqual(range(last, day(2026, 11, 10)), range(last, day(2026, 11, 11)));
  assert.equal(durationRange(last, day(2026, 11, 11)).to, '2026-11-11');
});

test('a report with a duration of its own uses it, dates entered at run time still win, and dates of its own win over nothing', () => {
  const today = day(2026, 11, 10);
  const saved = { from: '', to: '', last: { amount: 3, unit: 'months' } };
  assert.deepEqual(resolveDates(null, saved, today), { from: '2026-08-11', to: '2026-11-10' });
  assert.deepEqual(resolveDates({ from: '', to: '' }, saved, today), { from: '2026-08-11', to: '2026-11-10' });
  assert.deepEqual(resolveDates({ from: '2026-09-01', to: '2026-09-30' }, saved, today), { from: '2026-09-01', to: '2026-09-30' });
  assert.deepEqual(resolveDates({ from: '2026-09-01' }, saved, today), { from: '2026-09-01', to: '2026-11-10' });     // a start date alone runs to today
  assert.deepEqual(resolveDates(null, { from: '2026-01-01', to: '2026-01-31' }, today), { from: '2026-01-01', to: '2026-01-31' });
});

test('durations are described in words', () => {
  assert.equal(describeDuration({ amount: 3, unit: 'months' }), 'Last 3 months');
  assert.equal(describeDuration({ amount: 1, unit: 'weeks' }), 'Last 1 week');
  assert.equal(describeDuration({ amount: 30, unit: 'days' }), 'Last 30 days');
});
