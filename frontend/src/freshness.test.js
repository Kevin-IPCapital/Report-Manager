import assert from 'node:assert/strict';
import { test } from 'node:test';
import { basisLabel, failureNote, freshnessView } from './freshness.js';

test('a job that last worked days ago and has failed since is stale, with the last good time and the failures', () => {
  const view = freshnessView({ last_good_at: '2026-11-06T09:00:00+00:00', good_run_age_hours: 73, freshness: 'STALE', stale_since: '2026-11-06T10:00:00+00:00', failures_since_good: 4, data_age_hours: 0.1 });
  assert.equal(view.text, '3 days, 1 hour ago');
  assert.deepEqual([view.stale, view.basis, view.at, view.staleSince, view.failures], [true, 'last good run', '2026-11-06T09:00:00+00:00', '2026-11-06T10:00:00+00:00', 4]);
  assert.equal(failureNote(view), '4 failed runs since');
});

test('the stored timestamp does not override a last good run, however fresh it looks', () => {
  const view = freshnessView({ last_good_at: '2026-11-09T01:00:00+00:00', good_run_age_hours: 9, freshness: 'STALE', data_age_hours: 0.05, data_as_of: '2026-11-09T09:57:00+00:00' });
  assert.equal(view.text, '9 hours ago');
  assert.equal(view.basis, 'last good run');
});

test('recent good data with one failure since is current, and the single failure is worded in the singular', () => {
  const view = freshnessView({ last_good_at: 'x', good_run_age_hours: 0.5, freshness: 'CURRENT', failures_since_good: 1 });
  assert.deepEqual([view.stale, failureNote(view)], [false, '1 failed run since']);
});

test('with no good run in the log the stored timestamp is used, and labelled as such', () => {
  const view = freshnessView({ last_good_at: null, data_age_hours: 2, data_as_of: '2026-11-09T08:00:00+00:00', freshness: 'CURRENT', failures_since_good: 0 });
  assert.deepEqual([view.text, view.basis, view.stale, view.at], ['2 hours ago', 'stored data', false, '2026-11-09T08:00:00+00:00']);
  assert.match(basisLabel(view), /no good run in the log/);
});

test('a job that has never succeeded and has no stored data says so, and counts its failures', () => {
  const view = freshnessView({ last_good_at: null, data_age_hours: null, failures_since_good: 3 });
  assert.deepEqual([view.text, view.basis, view.stale], ['No successful run yet', 'none', false]);
  assert.equal(failureNote(view), '3 failed runs, none succeeded');
  assert.equal(basisLabel(view), '');
});

test('no failures gives no note, and missing counts are treated as none', () => {
  assert.equal(failureNote(freshnessView({ last_good_at: 'x', good_run_age_hours: 1, freshness: 'CURRENT' })), '');
  assert.equal(failureNote({ failures: 0, basis: 'none' }), '');
});

// ---- the "data as of" line
import { asOfView } from './freshness.js';
const stamp = () => 'STAMP';
const source = (extra) => ({ key: 'patches', label: 'Patch data', as_of: '2026-11-09T12:29:00+00:00', age_hours: 44, state: 'stale', failures_since: 3, basis: 'last good run', enabled: true, ...extra });

test('data that is as recent as expected reads as current, with how long ago', () => {
  const view = asOfView(source({ state: 'current', age_hours: 2, failures_since: 0 }), stamp);
  assert.equal(view.text, 'Patch data as of STAMP (2 hours ago)');
  assert.equal(view.tone, 'good');
  assert.match(view.title, /last collection that succeeded/);
});

test('stale data says so, and why: how many collections have failed since', () => {
  assert.equal(asOfView(source(), stamp).text, 'Patch data as of STAMP (1 day, 20 hours ago). Stale: the last 3 collections failed');
  assert.equal(asOfView(source({ failures_since: 1 }), stamp).text, 'Patch data as of STAMP (1 day, 20 hours ago). Stale: the last collection failed');
  assert.equal(asOfView(source({ failures_since: 0 }), stamp).text, 'Patch data as of STAMP (1 day, 20 hours ago). Stale: no collection has run since');
  assert.equal(asOfView(source(), stamp).tone, 'bad');
});

test('a page whose data has never been collected says so, and says if the collector is switched off', () => {
  const never = { as_of: null, age_hours: null, state: 'never', basis: 'none' };
  assert.equal(asOfView(source({ ...never, label: 'Backup data' }), stamp).text, 'Backup data: no data collected yet');
  assert.equal(asOfView(source({ ...never, label: 'Ticket data', enabled: false }), stamp).text, 'Ticket data: no data collected yet (the collector is switched off)');
  assert.equal(asOfView(source(never), stamp).tone, 'warn');
});

test('a time with no schedule to judge it by is shown plainly, neither good nor stale', () => {
  const view = asOfView(source({ state: 'unknown', age_hours: 900, failures_since: 0 }), stamp);
  assert.equal(view.tone, '');
  assert.ok(!view.text.includes('Stale'));
  assert.match(view.title, /no schedule/);
});

test('when the time comes from stored data the hover text says so', () => {
  assert.match(asOfView(source({ state: 'current', basis: 'stored data', age_hours: 5, failures_since: 0 }), stamp).title, /newest row stored/);
});

test('the real time formatting does not fall over on a bad value', () => {
  assert.match(asOfView(source({ state: 'current', as_of: 'not a date', failures_since: 0 })).text, /an unknown time/);
  assert.match(asOfView(source({ state: 'current', failures_since: 0 })).text, /2026/);
});
