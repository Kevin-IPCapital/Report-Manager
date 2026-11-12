import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CHOOSE_ONE, MAX_CHOICES, isChoices, keepKnown, labelOf, pickType, scopeParams, singleChoice, toggleChoice } from './overview.js';

test('a saved list of choices is only accepted if every one is a client or customer key, once each', () => {
  assert.equal(isChoices([]), true);
  assert.equal(isChoices(['client:3', 'org:30']), true);
  for (const bad of [null, 'client:3', [3], ['client:'], ['client:x'], ['other:1'], ['client:3', 'client:3'], ['client:3; drop'], [['client:3']], Array.from({ length: MAX_CHOICES + 1 }, (_, i) => `client:${i}`)]) {
    assert.equal(isChoices(bad), false, JSON.stringify(bad));
  }
});

test('choices become request parameters, leaving out whichever kind there are none of', () => {
  assert.deepEqual(scopeParams([]), {});
  assert.deepEqual(scopeParams(['client:3']), { client: '3' });
  assert.deepEqual(scopeParams(['client:3', 'org:30', 'client:5']), { client: '3,5', org: '30' });
  assert.deepEqual(scopeParams(['org:30', 'org:31']), { org: '30,31' });
});

test('clicking a name in the clients card adds it, and clicking it again removes it', () => {
  assert.deepEqual(toggleChoice([], 'client:3'), ['client:3']);
  assert.deepEqual(toggleChoice(['client:3'], 'client:5'), ['client:3', 'client:5']);
  assert.deepEqual(toggleChoice(['client:3', 'client:5'], 'client:3'), ['client:5']);
});

test('the list of choices cannot grow past the limit the server accepts', () => {
  const full = Array.from({ length: MAX_CHOICES }, (_, i) => `client:${i}`);
  assert.equal(toggleChoice(full, 'client:999').length, MAX_CHOICES);
});

test('choices that no longer exist are dropped, and the order of the rest is kept', () => {
  const options = [{ key: 'client:3', label: 'Acme' }, { key: 'org:30', label: 'Blue Crane' }];
  assert.deepEqual(keepKnown(['org:30', 'client:99', 'client:3'], options), ['org:30', 'client:3']);
  assert.deepEqual(keepKnown([], options), []);
  assert.deepEqual(keepKnown(['client:3'], []), []);
});

test('a bubble is labelled with the client name, or the key when the name is not known', () => {
  assert.equal(labelOf('client:3', [{ key: 'client:3', label: 'Acme' }]), 'Acme');
  assert.equal(labelOf('client:9', []), 'client:9');
});

test('choosing a measure in the combined group selects it, and choosing it again clears it, without changing who is chosen', () => {
  assert.deepEqual(pickType({ choices: ['client:3', 'client:5'], type: null }, 'third-party'), { choices: ['client:3', 'client:5'], type: 'third-party' });
  assert.deepEqual(pickType({ choices: ['client:3', 'client:5'], type: 'third-party' }, 'third-party'), { choices: ['client:3', 'client:5'], type: null });
  assert.deepEqual(pickType({ choices: [], type: 'a' }, 'b'), { choices: [], type: 'b' });
});

test('choosing a measure in a client\'s own group narrows the page to that client first', () => {
  assert.deepEqual(pickType({ choices: [], type: null }, 'windows-security', 'client:3'), { choices: ['client:3'], type: 'windows-security' });
  assert.deepEqual(pickType({ choices: ['client:3', 'client:5'], type: 'windows-security' }, 'windows-security', 'client:5'), { choices: ['client:5'], type: 'windows-security' });
});

test('choosing the measure already shown for the one chosen client switches it off', () => {
  assert.deepEqual(pickType({ choices: ['client:3'], type: 'windows-security' }, 'windows-security', 'client:3'), { choices: ['client:3'], type: null });
  assert.deepEqual(pickType({ choices: ['client:3'], type: 'windows-security' }, 'third-party', 'client:3'), { choices: ['client:3'], type: 'third-party' });
});

test('the Overall Score and the trend are for exactly one client, and the page says so otherwise', () => {
  assert.equal(singleChoice(['client:3']), 'client:3');
  assert.equal(singleChoice(['org:30']), 'org:30');                         // a customer not linked to a client is one too
  assert.equal(singleChoice([]), null);
  assert.equal(singleChoice(['client:3', 'client:5']), null);
  assert.equal(CHOOSE_ONE, 'Please select a single client');
});
