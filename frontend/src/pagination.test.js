import assert from 'node:assert/strict';
import { test } from 'node:test';
import { paginate } from './pagination.js';

const rows = Array.from({ length: 45 }, (_, i) => i + 1);

test('the first page holds the first rows and says which ones', () => {
  const p = paginate(rows, 0, 20);
  assert.deepEqual([p.items.length, p.items[0], p.page, p.pages, p.from, p.to, p.total], [20, 1, 0, 3, 1, 20, 45]);
});

test('the last page holds what is left', () => {
  const p = paginate(rows, 2, 20);
  assert.deepEqual([p.items, p.from, p.to], [[41, 42, 43, 44, 45], 41, 45]);
});

test('a page past the end is clamped to the last page, and a negative one to the first', () => {
  assert.equal(paginate(rows, 9, 20).page, 2);
  assert.equal(paginate(rows, -3, 20).page, 0);
});

test('an empty list is one empty page, not page 0 of 0', () => {
  const p = paginate([], 0, 20);
  assert.deepEqual([p.items, p.pages, p.from, p.to, p.total], [[], 1, 0, 0, 0]);
});

test('an exact multiple does not create an empty extra page', () => {
  assert.equal(paginate(rows.slice(0, 40), 0, 20).pages, 2);
});

test('a list that shrinks stays on a valid page', () => {
  assert.equal(paginate(rows.slice(0, 5), 2, 20).page, 0);
});
