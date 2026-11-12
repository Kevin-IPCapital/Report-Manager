import assert from 'node:assert/strict';
import { test } from 'node:test';
import { gapText, openDays, shortDate, sinceText } from './gaps.js';

test('each kind of gap is worded for the person reading it', () => {
  assert.equal(gapText({ kind: 'unlinked', source_label: 'Cove' }), 'Cove customer not linked to a client');
  assert.equal(gapText({ kind: 'missing', source_label: 'N-central' }), 'Client missing from N-central');
  assert.equal(gapText({ kind: 'stale', source_label: 'Autotask', external_id: '99' }), 'Link to Autotask 99 no longer exists');
  assert.equal(gapText({ kind: 'something new' }), 'Gap');
});

test('dates from the record are short and local, and missing or damaged ones give nothing', () => {
  assert.match(shortDate('2026-11-05T08:00:00Z'), /2026/);
  assert.match(shortDate('2026-11-05T08:00:00Z'), /Nov|11/);
  for (const bad of [null, undefined, '', 'not a date']) assert.equal(shortDate(bad), '');
  assert.match(sinceText('2026-11-05T08:00:00Z'), /^since /);
  assert.equal(sinceText(null), '');
});

test('how long a gap stayed open', () => {
  assert.equal(openDays('2026-11-01T08:00:00Z', '2026-11-10T09:00:00Z'), 9);
  assert.equal(openDays('2026-11-10T08:00:00Z', '2026-11-10T09:00:00Z'), 0);
  assert.equal(openDays('2026-11-10T09:00:00Z', '2026-11-01T08:00:00Z'), 0);        // never negative
  assert.equal(openDays(null, '2026-11-01T08:00:00Z'), null);
  assert.equal(openDays('2026-11-01T08:00:00Z', 'x'), null);
});
