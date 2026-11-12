import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { useAuth } from '../auth/AuthContext';
import { browserStore } from '../storage';

// useState that remembers its value between visits, separately for each signed-in person. `key` names the value (pass null to not
// remember it); `validate` says whether a remembered value is still usable, else `initial` is used. Only a change made by the person
// is saved, so defaults are never frozen in: if a default improves later, people who never changed it get the improvement.
export function usePersistentState(key, initial, validate) {
  const username = useAuth()?.user?.username;
  const store = useMemo(() => browserStore(username), [username]);
  const [value, setValue] = useState(() => (key == null ? initial : store.get(key, validate, initial)));
  const changed = useRef(false);
  const set = (next) => { changed.current = true; setValue(next); };
  useEffect(() => { if (changed.current && key != null) store.set(key, value); }, [value]);
  return [value, set];
}

// Forget everything remembered for this person (Settings has the button). How many entries went.
// What you saved on purpose (your own date ranges) is not a remembered input: forgetting the inputs must not delete it.
const saved = (name) => name.startsWith('ranges:');
export const forgetRemembered = (username) => browserStore(username).clear(saved);
export const rememberedCount = (username) => browserStore(username).keys().filter((name) => !saved(name)).length;
