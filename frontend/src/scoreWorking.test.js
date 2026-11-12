import assert from 'node:assert/strict';
import { test } from 'node:test';
import { HOW_IT_WORKS, formula, workingView } from './scoreWorking.js';

// the same worked example as backend/api/test_logic.py (ExplainScores), as the server sends it
const category = (id, name, compliance, weight, counts, share, reason = null) => ({ category_id: id, category: name, compliance, weight, counts, share, reason });
const calculation = {
  types: [
    { key: 'windows-security', label: 'Windows Security Patching', score: 93.33, weight: 1, counts: true, share: 25.0, reason: null, weighted_total: 280, weight_total: 3,
      categories: [category('a', 'Cumulative Updates', 96, 2, true, 66.7), category('b', 'Dot Net', 88, 1, true, 33.3)] },
    { key: 'windows-patching', label: 'Windows Patching', score: 50, weight: 1, counts: true, share: 25.0, reason: null, weighted_total: 50, weight_total: 1,
      categories: [category('c', 'Drivers', 50, 1, true, 100), category('d', 'Empty', null, 1, false, null, 'it lists no patches')] },
    { key: 'third-party', label: 'Third Party Patching', score: 70, weight: 2, counts: true, share: 50.0, reason: null, weighted_total: 70, weight_total: 1,
      categories: [category('e', 'Browsers', 90, 0, false, null, 'its weight is 0'), category('f', 'Office', 70, 1, true, 100)] },
  ],
  overall: { score: 70.83, weighted_total: 283.3333, weight_total: 4 },
};

test('the rule is explained in plain steps that state the rules that matter', () => {
  assert.equal(HOW_IT_WORKS.length, 6);
  for (const step of HOW_IT_WORKS) assert.match(step, /\.$/);
  const all = HOW_IT_WORKS.join(' ');
  for (const must of ['weighted', 'weight of 0', 'counts as 1', 'shared', 'report', 'previous score', 'not of patches']) assert.ok(all.includes(must), must);
});

test('a type with several categories shows the sum, the divisor and the result', () => {
  const items = [{ name: 'A', value: 96, weight: 2, counts: true }, { name: 'B', value: 88, weight: 1, counts: true }];
  assert.equal(formula(items, 93.33), '(96.00 × 2 + 88.00 × 1) ÷ (2 + 1) = 93.33%');
});

test('what is left out is not in the sum, and one item or none is said in words', () => {
  const left = [{ name: 'A', value: 90, weight: 0, counts: false }, { name: 'Office', value: 70, weight: 1, counts: true }];
  assert.equal(formula(left, 70), 'Only Office counts, so the score is 70.00%.');
  assert.equal(formula([{ name: 'A', value: null, weight: 1, counts: false }], null), 'Nothing counts, so there is no score.');
  assert.equal(formula([], null), 'Nothing counts, so there is no score.');
});

test('weights are written without trailing zeros', () => {
  const items = [{ name: 'A', value: 50, weight: 0.5, counts: true }, { name: 'B', value: 100, weight: 3, counts: true }];
  assert.equal(formula(items, 92.86), '(50.00 × 0.5 + 100.00 × 3) ÷ (0.5 + 3) = 92.86%');
});

test('the working for a score turns the server\'s explanation into rows and formulas', () => {
  const view = workingView(calculation);
  assert.deepEqual(view.types.map((t) => [t.label, t.scoreText, t.weightText, t.shareText]), [['Windows Security Patching', '93.33%', '1', '25%'], ['Windows Patching', '50.00%', '1', '25%'], ['Third Party Patching', '70.00%', '2', '50%']]);
  assert.deepEqual(view.types[0].rows.map((r) => [r.name, r.complianceText, r.weightText, r.shareText]), [['Cumulative Updates', '96.00%', '2', '66.7%'], ['Dot Net', '88.00%', '1', '33.3%']]);
  assert.equal(view.types[0].formula, '(96.00 × 2 + 88.00 × 1) ÷ (2 + 1) = 93.33%');
  assert.equal(view.overall.scoreText, '70.83%');
  assert.equal(view.overall.formula, '(93.33 × 1 + 50.00 × 1 + 70.00 × 2) ÷ (1 + 1 + 2) = 70.83%');
});

test('a left-out category says why, and has no share', () => {
  const rows = workingView(calculation).types;
  assert.deepEqual([rows[1].rows[1].counts, rows[1].rows[1].reason, rows[1].rows[1].shareText, rows[1].rows[1].complianceText], [false, 'left out: it lists no patches', '', '—']);
  assert.deepEqual([rows[2].rows[0].counts, rows[2].rows[0].reason], [false, 'left out: its weight is 0']);
  assert.equal(rows[2].rows[0].complianceText, '90.00%');                                      // still shown, so you can see what was left out
});

test('a type that is left out of the overall score says why', () => {
  const left = { ...calculation, types: calculation.types.map((t) => (t.key === 'windows-patching' ? { ...t, counts: false, share: null, reason: 'its weight is 0', weight: 0 } : t)) };
  const view = workingView(left);
  assert.equal(view.types[1].reason, 'left out: its weight is 0');
  assert.equal(view.overall.formula, '(93.33 × 1 + 70.00 × 2) ÷ (1 + 2) = 70.83%');
});

test('with nothing to score the overall result is a dash and a sentence', () => {
  const empty = { types: [{ key: 't', label: 'T', score: null, weight: 1, counts: false, share: null, reason: 'none of its categories counts', categories: [] }], overall: { score: null, weighted_total: 0, weight_total: 0 } };
  const view = workingView(empty);
  assert.equal(view.overall.scoreText, '—');
  assert.equal(view.overall.formula, 'Nothing counts, so there is no score.');
  assert.equal(view.types[0].formula, 'Nothing counts, so there is no score.');
});
