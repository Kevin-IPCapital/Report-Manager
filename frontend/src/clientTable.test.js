import assert from 'node:assert/strict';
import { test } from 'node:test';
import { linkedCustomers } from './clientTable.js';

const LABELS = { autotask: 'Autotask', ncentral: 'N-central', sentinelone: 'SentinelOne', cove: 'Cove' };

test('the column shows the customers\' names and none of their IDs', () => {
  const text = linkedCustomers([{ source: 'autotask', external_id: '101', customer_name: 'IP Capital: BITCO' }, { source: 'ncentral', external_id: '7', customer_name: 'BitCo' }], LABELS);
  assert.equal(text, 'IP Capital: BITCO · BitCo');
  assert.doesNotMatch(text, /101|\b7\b/);
});

test('the same name in two systems is shown once, and the order of the links is kept', () => {
  const links = [{ source: 'cove', customer_name: 'Acme' }, { source: 'ncentral', customer_name: 'Zed' }, { source: 'autotask', customer_name: 'Acme' }];
  assert.equal(linkedCustomers(links, LABELS), 'Acme · Zed');
});

test('a link with no readable name says which system and that the name is not available, and never shows its ID', () => {
  const text = linkedCustomers([{ source: 'cove', external_id: '55', customer_name: null }, { source: 'autotask', external_id: '9' }, { source: 'ncentral', external_id: '7', customer_name: '' }], LABELS);
  assert.equal(text, 'Cove: name not available · Autotask: name not available · N-central: name not available');
  assert.doesNotMatch(text, /55|\b9\b|\b7\b/);
});

test('a system the page does not know is shown as it is called, and no links is an empty cell', () => {
  assert.equal(linkedCustomers([{ source: 'newsys', customer_name: null }], LABELS), 'newsys: name not available');
  assert.equal(linkedCustomers([], LABELS), '');
  assert.equal(linkedCustomers(undefined, LABELS), '');
});
