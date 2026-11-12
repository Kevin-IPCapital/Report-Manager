import { createContext } from 'preact';
import { useContext, useState } from 'preact/hooks';
import { addDays, daysBetween, iso, subMonths } from '../dateMath';
import { decodeRange, encodeRange, isRange } from '../rangeMemory';
import { usePersistentState } from './persist';

const Ctx = createContext(null);
export const useDateRange = () => useContext(Ctx);

export { addDays, daysBetween, subMonths };

// `draft` is what the user is editing (slider / date inputs); `applied` is what data was fetched with.
// Nothing refetches until refresh() copies draft -> applied (or just bumps refreshKey to reload).
export function DateRangeProvider({ children }) {
  const today = iso(new Date());
  // The last range applied is remembered between visits: a range ending today as its length, so it is still "the last N days" next time.
  const [saved, setSaved] = usePersistentState('range', null, isRange);
  const [draft, setDraft] = useState(() => decodeRange(saved, today) ?? { from: subMonths(today, 1), to: today });
  const [applied, setApplied] = useState(draft);
  const [refreshKey, setRefreshKey] = useState(0);

  // Moving one end past the other drags it along.
  const setFrom = (v) => v && setDraft((r) => ({ from: v, to: v > r.to ? v : r.to }));
  const setTo = (v) => v && setDraft((r) => ({ from: v < r.from ? v : r.from, to: v }));
  const refresh = () => { setApplied(draft); setRefreshKey((k) => k + 1); setSaved(encodeRange(draft, today)); };
  const dirty = draft.from !== applied.from || draft.to !== applied.to;

  return (
    <Ctx.Provider value={{ draft, applied, today, windowStart: subMonths(today, 6), setDraft, setFrom, setTo, refresh, refreshKey, dirty }}>
      {children}
    </Ctx.Provider>
  );
}
