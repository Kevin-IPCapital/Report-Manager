import { presets } from '../report/dates';
import { savedPresets } from '../ranges';
import { useSavedRanges } from '../state/SavedRangesContext';
import { useDateRange, addDays, daysBetween } from '../state/DateRangeContext';

// Power BI style slider: two thumbs over the last 6 months, one step per day.
// It only edits the draft range; the data is not fetched until "Refresh data" is pressed.
function DateSlider({ from, to, windowStart, today, onChange }) {
  const span = daysBetween(windowStart, today);
  const clamp = (n) => Math.min(span, Math.max(0, n));
  const a = clamp(daysBetween(windowStart, from));
  const b = clamp(daysBetween(windowStart, to));
  const set = (lo, hi) => onChange(addDays(windowStart, lo), addDays(windowStart, hi));
  return (
    <div class="slider" role="group" aria-label="Quick range, last 6 months">
      <div class="slider-track"><div class="slider-fill" style={{ left: `${(a / span) * 100}%`, right: `${100 - (b / span) * 100}%` }} /></div>
      {/* When both thumbs coincide, keep the one that can still move on top. */}
      <input type="range" min="0" max={span} value={a} aria-label="Start date" style={{ zIndex: a > span / 2 ? 3 : 1 }}
        onInput={(e) => set(Math.min(+e.target.value, b), b)} />
      <input type="range" min="0" max={span} value={b} aria-label="End date" style={{ zIndex: 2 }}
        onInput={(e) => set(a, Math.max(+e.target.value, a))} />
      <div class="slider-ends"><span>{windowStart}</span><span>{today}</span></div>
    </div>
  );
}

export function DateRange() {
  const { draft, today, windowStart, setDraft, setFrom, setTo, refresh, dirty } = useDateRange();
  const { ranges } = useSavedRanges();
  return (
    <div class="daterange">
      <label class="field">From<input type="date" value={draft.from} max={draft.to} onChange={(e) => setFrom(e.target.value)} /></label>
      <label class="field">To<input type="date" value={draft.to} min={draft.from} max={today} onChange={(e) => setTo(e.target.value)} /></label>
      <button class={dirty ? 'btn primary' : 'btn'} onClick={refresh}>Refresh data</button>
      <div class="presets" role="group" aria-label="Quick ranges">
        {[...presets(), ...savedPresets(ranges)].map((p) => <button key={p.key} title={p.title} type="button" class={`btn small${draft.from === p.from && draft.to === p.to ? ' active' : ''}`} aria-pressed={draft.from === p.from && draft.to === p.to} onClick={() => setDraft({ from: p.from, to: p.to })}>{p.label}</button>)}
      </div>
      <DateSlider from={draft.from} to={draft.to} windowStart={windowStart} today={today} onChange={(from, to) => setDraft({ from, to })} />
    </div>
  );
}
