import { createContext } from 'preact';
import { useContext } from 'preact/hooks';
import { addRange, isSavedRanges, removeRange } from '../ranges';
import { usePersistentState } from './persist';

// The date ranges this person has named and saved (a length such as "last 3 months"; the dates are worked out whenever one is used). One list
// for the whole app, so a range added on the Settings page is on the date control, the report run and the trend at once. Kept in the browser.
const Ctx = createContext(null);

export function SavedRangesProvider({ children }) {
  const [ranges, setRanges] = usePersistentState('ranges:saved', [], isSavedRanges);
  // { ok: true } or { ok: false, error }: the Settings page shows the error
  const add = (range) => {
    const result = addRange(ranges, range);
    if (result.ok) setRanges(result.list);
    return result;
  };
  const remove = (name) => setRanges(removeRange(ranges, name));
  return <Ctx.Provider value={{ ranges, add, remove }}>{children}</Ctx.Provider>;
}

export const useSavedRanges = () => useContext(Ctx) ?? { ranges: [], add: () => ({ ok: false, error: 'Saved ranges are not available here.' }), remove: () => {} };
