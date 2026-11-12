import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatBytes, formatDuration, titleCase } from './text.js';

test('durations skip zero units and use singular forms', () => {
  assert.equal(formatDuration(1), '1 hour');
  assert.equal(formatDuration(25.5), '1 day, 1 hour, 30 min');
  assert.equal(formatDuration(12.35), '12 hours, 21 min');
});

test('leftover seconds round up to one more minute', () => {
  assert.equal(formatDuration(0.09), '6 min');
  assert.equal(formatDuration(49.0025), '2 days, 1 hour, 1 min');
});

test('no value is a dash, and zero is zero minutes', () => {
  assert.equal(formatDuration(null), '—');
  assert.equal(formatDuration(0), '0 min');
});

test('title case keeps acronyms', () => {
  assert.equal(titleCase('ticket by client'), 'Ticket By Client');
  assert.equal(titleCase('SLA missed'), 'SLA Missed');
});

test('bytes use binary units with two decimals, like the Cove collector', () => {
  assert.equal(formatBytes(3 * 1024 ** 3), '3.00 GB');
  assert.equal(formatBytes(1536), '1.50 KB');
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(512), '512 B');
  assert.equal(formatBytes(2 * 1024 ** 4), '2.00 TB');
  assert.equal(formatBytes(null), '—');
  assert.equal(formatBytes(''), '—');
});

test('N-central keeps its own spelling, and no other hyphenated word is changed', () => {
  assert.equal(titleCase('n-central connected'), 'N-central Connected');
  assert.equal(titleCase('N-central disconnected'), 'N-central Disconnected');
  assert.equal(titleCase('the n-central status'), 'The N-central Status');
  assert.equal(titleCase('out-of-date agents'), 'Out-Of-Date Agents');             // everything else is as before
  assert.equal(titleCase('SentinelOne connected'), 'SentinelOne Connected');
});
