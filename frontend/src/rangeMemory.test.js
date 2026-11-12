import assert from 'node:assert/strict';
import { test } from 'node:test';
import { addDays, daysBetween, isIsoDay, subMonths } from './dateMath.js';
import { decodeRange, encodeRange, isRange } from './rangeMemory.js';

test('a range that ends today is kept as its length, and comes back as the same length ending on the new today', () => {
  const saved = encodeRange({ from: '2026-10-11', to: '2026-11-10' }, '2026-11-10');
  assert.deepEqual(saved, { days: 30 });
  assert.deepEqual(decodeRange(saved, '2026-11-10'), { from: '2026-10-11', to: '2026-11-10' });
  assert.deepEqual(decodeRange(saved, '2026-11-25'), { from: '2026-10-26', to: '2026-11-25' });   // two weeks later: still "the last 30 days"
});

test('a range that ends on another day keeps its dates', () => {
  const saved = encodeRange({ from: '2026-10-01', to: '2026-10-31' }, '2026-11-10');
  assert.deepEqual(saved, { from: '2026-10-01', to: '2026-10-31' });
  assert.deepEqual(decodeRange(saved, '2027-03-01'), { from: '2026-10-01', to: '2026-10-31' });
});

test('a single day ending today is zero days', () => {
  assert.deepEqual(encodeRange({ from: '2026-11-10', to: '2026-11-10' }, '2026-11-10'), { days: 0 });
  assert.deepEqual(decodeRange({ days: 0 }, '2026-11-10'), { from: '2026-11-10', to: '2026-11-10' });
});

test('the length survives month ends, year ends and leap days', () => {
  assert.deepEqual(decodeRange({ days: 59 }, '2028-03-01'), { from: '2028-01-02', to: '2028-03-01' });
  assert.deepEqual(decodeRange({ days: 30 }, '2027-01-05'), { from: '2026-12-06', to: '2027-01-05' });
  assert.equal(daysBetween('2028-01-02', '2028-03-01'), 59);
});

test('a range that cannot be used is not remembered, and a remembered one that cannot be used is ignored', () => {
  assert.equal(encodeRange({ from: '2026-11-10', to: '2026-10-01' }, '2026-11-10'), null);       // backwards
  assert.equal(encodeRange({ from: 'x', to: '2026-10-01' }, '2026-11-10'), null);
  assert.equal(encodeRange(null, '2026-11-10'), null);
  for (const bad of [null, undefined, 'x', 5, [], {}, { days: -1 }, { days: 1.5 }, { days: 99999 }, { days: '30' }, { days: 5, from: '2026-01-01' },
    { from: '2026-02-30', to: '2026-03-01' }, { from: '2026-03-02', to: '2026-03-01' }, { from: '2026-01-01' }, { from: '2026-01-01', to: '2026-01-02', extra: 1 }]) {
    assert.equal(isRange(bad), false, JSON.stringify(bad));
    assert.equal(decodeRange(bad, '2026-11-10'), null, JSON.stringify(bad));
  }
  assert.equal(isRange({ days: 0 }), true);
  assert.equal(isRange({ from: '2026-01-01', to: '2026-01-01' }), true);
});

test('date helpers: real days only, month arithmetic clamps to the shorter month', () => {
  assert.equal(isIsoDay('2026-02-28'), true);
  assert.equal(isIsoDay('2026-02-29'), false);
  assert.equal(isIsoDay('2028-02-29'), true);
  assert.equal(isIsoDay('2026-13-01'), false);
  assert.equal(isIsoDay('26-1-1'), false);
  assert.equal(isIsoDay(20261110), false);
  assert.equal(subMonths('2026-03-31', 1), '2026-02-28');
  assert.equal(subMonths('2026-01-15', 2), '2025-11-15');
  assert.equal(addDays('2026-12-31', 1), '2027-01-01');
});
