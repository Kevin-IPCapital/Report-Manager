import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { BLOCK_FILTERS, BLOCK_TYPES, DEFAULT_CONFIG, DURATION_UNITS, MAX_BLOCKS, MAX_FILTER_VALUES, filterValues, newBlock, validateTemplate } from './config.js';

const cases = JSON.parse(readFileSync(new URL('./template-cases.json', import.meta.url), 'utf8')).cases;

// The server (backend/api/test_reports.py) runs these same cases through its own copy of the rules.
for (const c of cases) {
  test(`shared case: ${c.name}`, () => {
    if (c.error) return assert.throws(() => validateTemplate(c.input), Error);
    const result = validateTemplate(c.input);
    if (c.expect) return assert.deepEqual(result, c.expect);
    for (const [key, value] of Object.entries(c.expect_field ?? {})) assert.deepEqual(result[key], value, key);
    for (const [key, value] of Object.entries(c.expect_block_filter ?? {})) assert.deepEqual(result.blocks[0].filters[key], value, key);
    return undefined;
  });
}

test('a normalised template is unchanged by validating it again', () => {
  for (const c of cases.filter((x) => x.expect)) assert.deepEqual(validateTemplate(c.expect), c.expect, c.name);
  assert.deepEqual(validateTemplate(DEFAULT_CONFIG), DEFAULT_CONFIG);
});

test('a title is cut at 120 characters, and a block title over 120 is refused', () => {
  assert.equal(validateTemplate({ version: 2, title: 'x'.repeat(130), blocks: [{ type: 'backups' }] }).title.length, 120);
  assert.throws(() => validateTemplate({ version: 2, blocks: [{ type: 'backups', title: 'x'.repeat(121) }] }));
});

test('a filter value over 200 characters is refused', () => {
  assert.throws(() => validateTemplate({ version: 2, blocks: [{ type: 'tickets', filters: { customer: 'x'.repeat(201) } }] }));
  assert.doesNotThrow(() => validateTemplate({ version: 2, blocks: [{ type: 'tickets', filters: { customer: 'x'.repeat(200) } }] }));
});

test('at most 20 blocks', () => {
  const blocks = (n) => Array.from({ length: n }, () => ({ type: 'backups' }));
  assert.equal(validateTemplate({ version: 2, blocks: blocks(MAX_BLOCKS) }).blocks.length, 20);
  assert.throws(() => validateTemplate({ version: 2, blocks: blocks(MAX_BLOCKS + 1) }));
});

test('more than 500 weights are refused, and 500 are allowed', () => {
  const weights = (n) => Object.fromEntries(Array.from({ length: n }, (_, i) => [`k${i}`, 1]));
  assert.doesNotThrow(() => validateTemplate({ version: 2, weights: weights(500), blocks: [{ type: 'patches' }] }));
  assert.throws(() => validateTemplate({ version: 2, weights: weights(501), blocks: [{ type: 'patches' }] }));
});

test('every block type has filters, and new blocks start with them all empty', () => {
  for (const type of BLOCK_TYPES) {
    assert.ok(BLOCK_FILTERS[type].length > 0, type);
    assert.deepEqual(Object.keys(newBlock(type, 'x').filters), BLOCK_FILTERS[type]);
    assert.ok(Object.values(newBlock(type, 'x').filters).every((v) => v === ''));
  }
});

test('a template with an unknown extra field is accepted and the field is dropped', () => {
  const result = validateTemplate({ version: 2, surprise: 1, blocks: [{ type: 'backups', colour: 'red' }] });
  assert.equal('surprise' in result, false);
  assert.equal('colour' in result.blocks[0], false);
});

test('a filter can hold 20 values and not 21, and a value over 200 characters is refused', () => {
  const list = (n) => Array.from({ length: n }, (_, i) => `Customer ${i}`);
  const make = (customer) => validateTemplate({ version: 2, blocks: [{ type: 'tickets', filters: { customer } }] }).blocks[0].filters.customer;
  assert.equal(make(list(MAX_FILTER_VALUES)).length, 20);
  assert.throws(() => make(list(MAX_FILTER_VALUES + 1)));
  assert.throws(() => make(['x'.repeat(201)]));
  assert.equal(make(['x'.repeat(200), 'y']).length, 2);
});

test('repeats are dropped before the limit is applied to what is kept, but the list length itself is limited', () => {
  const dup = Array.from({ length: 21 }, () => 'Acme');
  assert.throws(() => validateTemplate({ version: 2, blocks: [{ type: 'tickets', filters: { customer: dup } }] }));   // 21 entered, even though they are all the same
});

test('the values of a filter as a list', () => {
  assert.deepEqual(filterValues(''), []);
  assert.deepEqual(filterValues('Acme'), ['Acme']);
  assert.deepEqual(filterValues(['Acme', ' ', 'Zed']), ['Acme', 'Zed']);
  assert.deepEqual(filterValues(undefined), []);
  assert.deepEqual(filterValues(null), []);
});

test('a template with a single-value filter is unchanged by the new rules', () => {
  const t = validateTemplate({ version: 2, blocks: [{ type: 'tickets', filters: { customer: 'Acme' } }] });
  assert.equal(t.blocks[0].filters.customer, 'Acme');
  assert.deepEqual(validateTemplate(t), t);
});

test('each duration unit allows up to its own maximum and not one more', () => {
  const make = (amount, unit) => validateTemplate({ version: 2, period: { last: { amount, unit } }, blocks: [{ type: 'backups' }] }).period.last;
  for (const [unit, max] of Object.entries(DURATION_UNITS)) {
    assert.deepEqual(make(max, unit), { amount: max, unit });
    assert.throws(() => make(max + 1, unit), undefined, `${max + 1} ${unit}`);
    assert.deepEqual(make(1, unit), { amount: 1, unit });
  }
});

test('a normalised template with a duration is unchanged by validating it again, and one without has no duration field at all', () => {
  const t = validateTemplate({ version: 2, period: { last: { amount: 6, unit: 'months' } }, blocks: [{ type: 'backups' }] });
  assert.deepEqual(validateTemplate(t), t);
  assert.equal('last' in validateTemplate({ version: 2, blocks: [{ type: 'backups' }] }).period, false);
});
