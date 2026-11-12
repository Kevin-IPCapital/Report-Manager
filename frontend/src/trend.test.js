import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { TREND_RANGE_KEYS, isTrendRangeKey, limitTrend, ticketTrend, trendRangeOptions, weekStart } from './trend.js';

const trend = { x: ['2025-10-01', '2026-02-10', '2026-05-09', '2026-05-10', '2026-08-15', '2026-11-10'], y: [1, 2, 3, 4, 5, 6] };

test('all history keeps every point, as a copy', () => {
  const kept = limitTrend(trend, 'all', '2026-11-10');
  assert.deepEqual(kept, trend);
  assert.notEqual(kept.x, trend.x);
});

test('a range keeps the points from the day after the same date that many months back, up to today', () => {
  assert.deepEqual(limitTrend(trend, '6m', '2026-11-10'), { x: ['2026-08-15', '2026-11-10'], y: [5, 6] });     // 6 months back from 10 Nov is 10 May, so it starts on 11 May: the point on 10 May is out
  assert.deepEqual(limitTrend(trend, '3m', '2026-11-10'), { x: ['2026-08-15', '2026-11-10'], y: [5, 6] });
  assert.deepEqual(limitTrend(trend, '12m', '2026-11-10').y, [2, 3, 4, 5, 6]);
});

test('points after today are left out, and an unknown range shows everything', () => {
  assert.deepEqual(limitTrend({ x: ['2026-11-10', '2026-11-12'], y: [1, 2] }, '3m', '2026-11-10'), { x: ['2026-11-10'], y: [1] });
  assert.deepEqual(limitTrend(trend, 'nonsense', '2026-11-10'), trend);
});

test('an empty or missing trend stays empty and the input is not changed', () => {
  assert.deepEqual(limitTrend({ x: [], y: [] }, '3m', '2026-11-10'), { x: [], y: [] });
  assert.deepEqual(limitTrend(null, '3m', '2026-11-10'), { x: [], y: [] });
  const before = JSON.stringify(trend);
  limitTrend(trend, '3m', '2026-11-10');
  assert.equal(JSON.stringify(trend), before);
  assert.deepEqual(TREND_RANGE_KEYS, ['all', '3m', '6m', '12m']);
});

const at = (day) => ({ created_at: `${day}T08:00:00Z` });

test('a short period is shown per day, with empty days included', () => {
  const result = ticketTrend([at('2026-10-01'), at('2026-10-01'), at('2026-10-03')], '2026-10-01', '2026-10-04');
  assert.deepEqual(result.x, ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04']);
  assert.deepEqual(result.y, [2, 0, 1, 0]);
  assert.equal(result.weekly, false);
  assert.equal(result.heading, 'Tickets Created Per Day');
});

test('62 days is still daily and 63 is weekly', () => {
  assert.equal(ticketTrend([], '2026-09-01', '2026-11-01').weekly, false);       // 62 days
  assert.equal(ticketTrend([], '2026-09-01', '2026-11-02').weekly, true);        // 63 days
});

test('a long period is shown per week starting Monday, including the partial first week', () => {
  const rows = [at('2026-08-05'), at('2026-08-09'), at('2026-08-10'), at('2026-10-30')];
  const result = ticketTrend(rows, '2026-08-05', '2026-10-31');                   // 5 Aug 2026 is a Wednesday
  assert.equal(result.x[0], '2026-08-03');
  assert.deepEqual(result.y.slice(0, 3), [2, 1, 0]);                              // 3 Aug week: the 5th and the 9th; 10 Aug week: the 10th
  assert.equal(result.x.at(-1), '2026-10-26');
  assert.equal(result.y.at(-1), 1);
  assert.equal(result.y.reduce((a, b) => a + b, 0), 4);
  assert.ok(result.x.every((w) => weekStart(w) === w), 'every label is a Monday');
});

test('tickets outside the period, or without a date, are not counted', () => {
  const result = ticketTrend([at('2026-09-30'), at('2026-10-05'), at('2026-11-01'), { created_at: null }, {}], '2026-10-01', '2026-10-31');
  assert.equal(result.y.reduce((a, b) => a + b, 0), 1);
});

test('weeks start on Monday whatever day it is', () => {
  assert.deepEqual(['2026-11-09', '2026-11-10', '2026-11-15', '2026-11-16', '2027-01-01'].map(weekStart), ['2026-11-09', '2026-11-09', '2026-11-09', '2026-11-16', '2026-12-28']);
});

test('the real ticket rows can be bucketed, and every ticket in the period is counted exactly once', () => {
  const real = JSON.parse(readFileSync(new URL('./report/fixtures/api-responses.json', import.meta.url), 'utf8')).tickets.tickets.rows;
  const result = ticketTrend(real, '2026-10-01', '2026-10-31');
  assert.equal(result.y.reduce((a, b) => a + b, 0), real.filter((r) => r.created_at.slice(0, 10) >= '2026-10-01' && r.created_at.slice(0, 10) <= '2026-10-31').length);
});

const saved = [{ name: 'Board pack', amount: 100, unit: 'days' }, { name: 'Quarter', amount: 3, unit: 'months' }];

test('a range you saved limits the trend exactly as a built-in range of the same length does', () => {
  assert.deepEqual(limitTrend(trend, 'saved:Quarter', '2026-11-10', saved), limitTrend(trend, '3m', '2026-11-10'));
  assert.deepEqual(limitTrend(trend, 'saved:Board pack', '2026-11-10', saved).x, ['2026-08-15', '2026-11-10']);          // 100 days: from 3 Aug
  assert.deepEqual(limitTrend({ x: ['2026-08-02', '2026-08-03'], y: [1, 2] }, 'saved:Board pack', '2026-11-10', saved).x, ['2026-08-03']);
});

test('a saved range that no longer exists, or an unknown one, shows everything rather than nothing', () => {
  assert.deepEqual(limitTrend(trend, 'saved:Deleted', '2026-11-10', saved), trend);
  assert.deepEqual(limitTrend(trend, 'saved:Quarter', '2026-11-10', []), trend);
});

test('the choices are the built-in ranges and then yours, and what may be remembered', () => {
  assert.deepEqual(trendRangeOptions(saved).map((o) => o.key), ['all', '3m', '6m', '12m', 'saved:Board pack', 'saved:Quarter']);
  assert.equal(trendRangeOptions(saved).at(-1).label, 'Quarter');
  assert.deepEqual(trendRangeOptions().map((o) => o.key), TREND_RANGE_KEYS);
  assert.deepEqual(['all', '6m', 'saved:Quarter', 'saved:', 'saved:' + 'x'.repeat(41), 'nonsense', null, 5].map(isTrendRangeKey), [true, true, true, false, false, false, false, false]);
});
