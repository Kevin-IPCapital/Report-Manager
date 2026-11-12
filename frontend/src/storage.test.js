import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createStore } from './storage.js';

// A stand-in for localStorage.
function fakeBackend(options = {}) {
  const data = new Map();
  return {
    data,
    get length() { return data.size; },
    key: (i) => [...data.keys()][i] ?? null,
    getItem: (k) => { if (options.broken) throw new Error('blocked'); return data.has(k) ? data.get(k) : null; },
    setItem: (k, v) => { if (options.broken || options.full) throw new Error('quota'); data.set(k, v); },
    removeItem: (k) => { data.delete(k); },
  };
}

test('what is saved is read back, and a missing value gives the fallback', () => {
  const store = createStore(fakeBackend(), 'kev');
  assert.equal(store.set('tickets:filters', { priority: 'High', open: true }), true);
  assert.deepEqual(store.get('tickets:filters'), { priority: 'High', open: true });
  assert.equal(store.get('nothing', () => true, 'default'), 'default');
  assert.equal(store.get('nothing'), undefined);
});

test('each person has their own remembered values, even on the same browser', () => {
  const backend = fakeBackend();
  const kev = createStore(backend, 'Kev'), sam = createStore(backend, 'sam');
  kev.set('range', { days: 30 });
  assert.deepEqual(kev.get('range'), { days: 30 });
  assert.equal(sam.get('range', () => true, null), null);
  assert.deepEqual(createStore(backend, '  KEV ').get('range'), { days: 30 });      // the name is not case or space sensitive
});

test('a value that fails its check is ignored', () => {
  const store = createStore(fakeBackend(), 'kev');
  store.set('size', 7);
  assert.equal(store.get('size', (v) => [10, 20, 50].includes(v), 20), 20);
  assert.equal(store.get('size', (v) => v === 7, 20), 7);
});

test('damaged, foreign or out-of-date entries are ignored instead of breaking anything', () => {
  const backend = fakeBackend();
  const store = createStore(backend, 'kev');
  for (const raw of ['not json', '{', 'null', '"text"', '42', '{"v":2,"value":1}', '{"v":1}', '{"value":1}', '[]']) {
    backend.data.set('rm:v1:kev:k', raw);
    assert.equal(store.get('k', () => true, 'fallback'), 'fallback', raw);
  }
});

test('a browser that blocks storage remembers nothing and does not throw', () => {
  const store = createStore(fakeBackend({ broken: true }), 'kev');
  assert.equal(store.set('k', 1), false);
  assert.equal(store.get('k', () => true, 'fallback'), 'fallback');
  assert.deepEqual(store.keys(), []);
  assert.equal(store.clear(), 0);
});

test('a full browser store refuses the write and says so', () => {
  assert.equal(createStore(fakeBackend({ full: true }), 'kev').set('k', 1), false);
});

test('a value that is too large is refused, and one that cannot be written as JSON is too', () => {
  const store = createStore(fakeBackend(), 'kev');
  assert.equal(store.set('big', 'x'.repeat(30000)), false);
  assert.equal(store.get('big', () => true, 'none'), 'none');
  const circular = {}; circular.self = circular;
  assert.equal(store.set('loop', circular), false);
  assert.equal(store.set('undefined', undefined), true);
  assert.equal(store.get('undefined', () => true, 'fallback'), 'fallback');       // nothing was really kept
});

test('clearing forgets this person\'s entries and nobody else\'s, and leaves other keys of the browser alone', () => {
  const backend = fakeBackend();
  backend.data.set('ops_theme', '"dark"');
  const kev = createStore(backend, 'kev'), sam = createStore(backend, 'sam');
  kev.set('a', 1); kev.set('b', 2); sam.set('a', 3);
  assert.deepEqual(kev.keys().sort(), ['a', 'b']);
  assert.equal(kev.clear(), 2);
  assert.deepEqual(kev.keys(), []);
  assert.equal(sam.get('a'), 3);
  assert.equal(backend.data.get('ops_theme'), '"dark"');
});

test('a name that looks like another person\'s prefix cannot reach their entries', () => {
  const backend = fakeBackend();
  createStore(backend, 'kev').set('a', 1);
  const sneaky = createStore(backend, 'kev:a');
  assert.equal(sneaky.get('a', () => true, 'none'), 'none');
  assert.deepEqual(sneaky.keys(), []);
});

test('removing one entry', () => {
  const store = createStore(fakeBackend(), 'kev');
  store.set('a', 1); store.set('b', 2); store.remove('a');
  assert.deepEqual(store.keys(), ['b']);
});

test('forgetting can keep some names: only the others go, and the count is of those that went', () => {
  const backend = fakeBackend();
  const store = createStore(backend, 'kev');
  store.set('range', { days: 30 }); store.set('tickets:filters', { a: 'b' }); store.set('ranges:saved', [{ name: 'Q' }]);
  assert.equal(store.clear((name) => name.startsWith('ranges:')), 2);
  assert.deepEqual(store.keys(), ['ranges:saved']);
  assert.deepEqual(store.get('ranges:saved'), [{ name: 'Q' }]);
  assert.equal(store.clear(), 1);                                                    // with nothing to keep, everything goes as before
  assert.deepEqual(store.keys(), []);
});
