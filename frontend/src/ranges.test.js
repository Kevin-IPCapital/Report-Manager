import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MAX_RANGE_NAME, MAX_SAVED_RANGES, addRange, isDuration, isSavedRange, isSavedRanges, removeRange, savedPresets } from './ranges.js';
import { durationStart } from './dateMath.js';

const range = (name, amount = 3, unit = 'months') => ({ name, amount, unit });

test('a length is whole days, weeks or months within the limit for the unit', () => {
  assert.equal(isDuration({ amount: 1, unit: 'days' }), true);
  assert.equal(isDuration({ amount: 3660, unit: 'days' }), true);
  assert.equal(isDuration({ amount: 520, unit: 'weeks' }), true);
  assert.equal(isDuration({ amount: 120, unit: 'months' }), true);
  for (const bad of [null, [], {}, { amount: 0, unit: 'days' }, { amount: 1.5, unit: 'days' }, { amount: '3', unit: 'days' }, { amount: 3, unit: 'years' }, { amount: 3, unit: 'toString' },
    { amount: 3661, unit: 'days' }, { amount: 521, unit: 'weeks' }, { amount: 121, unit: 'months' }, { amount: -1, unit: 'days' }]) assert.equal(isDuration(bad), false, JSON.stringify(bad));
});

test('a saved range has exactly a name, an amount and a unit', () => {
  assert.equal(isSavedRange(range('Quarter')), true);
  for (const bad of [range(''), range(' padded '), range('x'.repeat(MAX_RANGE_NAME + 1)), range('Q', 0), range('Q', 3, 'years'), { ...range('Q'), extra: 1 }, { name: 'Q', amount: 3 }, null, 'Q']) {
    assert.equal(isSavedRange(bad), false, JSON.stringify(bad));
  }
  assert.equal(isSavedRange(range('x'.repeat(MAX_RANGE_NAME))), true);
});

test('what comes back from storage must be a short list of valid ranges with different names', () => {
  assert.equal(isSavedRanges([]), true);
  assert.equal(isSavedRanges([range('A'), range('B')]), true);
  assert.equal(isSavedRanges([range('A'), range('a')]), false);                        // the same name ignoring case
  assert.equal(isSavedRanges([range('A'), { bad: true }]), false);
  assert.equal(isSavedRanges('A'), false);
  assert.equal(isSavedRanges(Array.from({ length: MAX_SAVED_RANGES + 1 }, (_, i) => range(`R${i}`))), false);
  assert.equal(isSavedRanges(Array.from({ length: MAX_SAVED_RANGES }, (_, i) => range(`R${i}`))), true);
});

test('adding a range trims the name, accepts numbers typed as text, and leaves the list it was given alone', () => {
  const list = [range('A')];
  const result = addRange(list, { name: '  Board pack ', amount: '100', unit: 'days' });
  assert.deepEqual(result, { ok: true, list: [range('A'), { name: 'Board pack', amount: 100, unit: 'days' }] });
  assert.deepEqual(list, [range('A')]);
});

test('each way a range can be refused says why', () => {
  const list = [range('Quarter')];
  assert.equal(addRange(list, { name: '', amount: 3, unit: 'months' }).error, 'Give the range a name.');
  assert.equal(addRange(list, { name: '   ', amount: 3, unit: 'months' }).error, 'Give the range a name.');
  assert.match(addRange(list, { name: 'x'.repeat(41), amount: 3, unit: 'months' }).error, /at most 40/);
  assert.equal(addRange(list, { name: 'Y', amount: 3, unit: 'years' }).error, 'Choose days, weeks or months.');
  assert.equal(addRange(list, { name: 'Y', amount: 0, unit: 'days' }).error, 'A range is from 1 to 3660 days.');
  assert.equal(addRange(list, { name: 'Y', amount: 2.5, unit: 'weeks' }).error, 'A range is from 1 to 520 weeks.');
  assert.equal(addRange(list, { name: 'Y', amount: '', unit: 'months' }).error, 'A range is from 1 to 120 months.');
  assert.equal(addRange(list, { name: ' QUARTER ', amount: 6, unit: 'months' }).error, 'You already have a range with that name.');
  const full = Array.from({ length: MAX_SAVED_RANGES }, (_, i) => range(`R${i}`));
  assert.match(addRange(full, { name: 'One more', amount: 1, unit: 'days' }).error, /At most 20/);
});

test('removing a range ignores case and spaces, and a name that is not there changes nothing', () => {
  const list = [range('Quarter'), range('Board pack')];
  assert.deepEqual(removeRange(list, ' quarter '), [range('Board pack')]);
  assert.deepEqual(removeRange(list, 'Nothing'), list);
});

test('the quick buttons for saved ranges are worked out for the day they are used, and look like the built-in ones', () => {
  const presets = savedPresets([range('Quarter'), range('Board pack', 100, 'days')], new Date(2026, 10, 10));      // 10 November 2026
  assert.deepEqual(presets.map((p) => [p.key, p.label, p.from, p.to]), [['saved:Quarter', 'Quarter', '2026-08-11', '2026-11-10'], ['saved:Board pack', 'Board pack', '2026-08-03', '2026-11-10']]);
  assert.equal(presets[0].title, 'Last 3 months');
  assert.equal(savedPresets([range('Quarter')], new Date(2027, 0, 5))[0].from, '2026-10-06');                         // the same range, another day: other dates
  assert.deepEqual(savedPresets([]), []);
});

test('there is one way to count the start of the last N days, weeks or months', () => {
  assert.equal(durationStart({ amount: 30, unit: 'days' }, '2026-11-10'), '2026-10-12');
  assert.equal(durationStart({ amount: 1, unit: 'days' }, '2026-11-10'), '2026-11-10');
  assert.equal(durationStart({ amount: 2, unit: 'weeks' }, '2026-11-10'), '2026-10-28');
  assert.equal(durationStart({ amount: 3, unit: 'months' }, '2026-11-10'), '2026-08-11');
  assert.equal(durationStart({ amount: 1, unit: 'months' }, '2026-03-31'), '2026-03-01');                             // 31 March less a month is 28 February
  assert.equal(durationStart({ amount: 12, unit: 'months' }, '2028-02-29'), '2027-03-01');                            // across a leap day
});
