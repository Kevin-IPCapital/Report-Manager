import assert from 'node:assert/strict';
import { test } from 'node:test';
import { isFilters, isOneOf, isPageSize, isSort, isText, usableSort } from './persisted.js';

test('filters: names to text or true, in a sensible size', () => {
  assert.equal(isFilters({}), true);
  assert.equal(isFilters({ priority: 'High', open: true }), true);
  for (const bad of [null, [], 'x', 5, { a: false }, { a: 1 }, { a: '' }, { a: null }, { a: {} }, { '': 'x' }, { a: 'x'.repeat(201) }, { ['k'.repeat(61)]: 'x' }]) assert.equal(isFilters(bad), false, JSON.stringify(bad));
  assert.equal(isFilters(Object.fromEntries(Array.from({ length: 51 }, (_, i) => [`k${i}`, 'v']))), false);
});

test('sorts: a column and a direction, or none', () => {
  assert.equal(isSort(null), true);
  assert.equal(isSort({ key: 'priority', dir: 1 }), true);
  assert.equal(isSort({ key: 'priority', dir: -1 }), true);
  for (const bad of [undefined, 'x', {}, { key: 'a' }, { key: 'a', dir: 0 }, { key: 'a', dir: '1' }, { key: '', dir: 1 }, { key: 'a', dir: 1, extra: 1 }, { dir: 1 }]) assert.equal(isSort(bad), false, JSON.stringify(bad));
});

test('a remembered sort is used only while its column still exists', () => {
  const columns = [{ key: 'priority' }, { key: 'title' }];
  assert.deepEqual(usableSort({ key: 'title', dir: 1 }, columns), { key: 'title', dir: 1 });
  assert.equal(usableSort({ key: 'gone', dir: 1 }, columns), null);
  assert.equal(usableSort(null, columns), null);
});

test('page sizes, short text and a fixed list of options', () => {
  assert.equal(isPageSize()(20), true);
  assert.equal(isPageSize()(7), false);
  assert.equal(isPageSize([12, 24])(24), true);
  assert.equal(isPageSize([12, 24])(20), false);
  assert.equal(isText('Acme'), true);
  assert.equal(isText('x'.repeat(201)), false);
  assert.equal(isText(5), false);
  assert.equal(isOneOf(['worst', 'name'])('name'), true);
  assert.equal(isOneOf(['worst', 'name'])('other'), false);
});
