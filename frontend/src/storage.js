// What the app remembers between visits (dates, filters, sorting, page sizes), kept in the browser and separately for each person who
// signs in on it. Every read is checked, so a damaged or out-of-date entry is ignored instead of breaking a page, and a browser that
// blocks storage simply remembers nothing. To keep these on the server instead, replace this file: nothing else knows where they live.
const PREFIX = 'rm:v1:';
const MAX_CHARS = 20000;   // one remembered value is small; anything bigger is refused

const scopeOf = (username) => encodeURIComponent(String(username ?? '').trim().toLowerCase() || 'anonymous');

// `backend` is anything shaped like localStorage (getItem, setItem, removeItem, key, length).
export function createStore(backend, username) {
  const prefix = `${PREFIX}${scopeOf(username)}:`;
  const keysOf = () => {
    const keys = [];
    try { for (let i = 0; i < backend.length; i += 1) { const k = backend.key(i); if (k && k.startsWith(prefix)) keys.push(k); } } catch { /* storage unavailable */ }
    return keys;
  };
  return {
    // The remembered value if there is one and `validate` accepts it; otherwise `fallback`.
    get(key, validate = () => true, fallback = undefined) {
      try {
        const raw = backend.getItem(prefix + key);
        if (raw == null) return fallback;
        const entry = JSON.parse(raw);
        if (!entry || entry.v !== 1 || !('value' in entry)) return fallback;
        return validate(entry.value) ? entry.value : fallback;
      } catch { return fallback; }
    },
    set(key, value) {
      try {
        const text = JSON.stringify({ v: 1, saved: Date.now(), value });
        if (text.length > MAX_CHARS) return false;
        backend.setItem(prefix + key, text);
        return true;
      } catch { return false; }
    },
    remove(key) { try { backend.removeItem(prefix + key); } catch { /* nothing to do */ } },
    keys: () => keysOf().map((k) => k.slice(prefix.length)),
    // Forget what is remembered for this person, except what `keep` says to keep (it is given the name without the prefix); how many entries went.
    clear(keep = () => false) {
      const keys = keysOf().filter((k) => !keep(k.slice(prefix.length)));
      for (const k of keys) { try { backend.removeItem(k); } catch { /* nothing to do */ } }
      return keys.length;
    },
  };
}

const unavailable = { length: 0, key: () => null, getItem: () => null, setItem: () => { throw new Error('no storage'); }, removeItem: () => {} };
export const browserStore = (username) => createStore(typeof localStorage === 'undefined' ? unavailable : localStorage, username);
