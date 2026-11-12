import { useState } from 'preact/hooks';
import { useAuth } from '../auth/AuthContext';
import { PageHeader } from '../components/PageHeader';
import { describeDuration } from '../report/dates';
import { DURATION_UNITS } from '../report/config';
import { MAX_RANGE_NAME, MAX_SAVED_RANGES } from '../ranges';
import { forgetRemembered, rememberedCount } from '../state/persist';
import { useSavedRanges } from '../state/SavedRangesContext';
import { useSettings } from '../state/SettingsContext';
import { accents } from '../theme';

const themes = [['system', 'Match device'], ['light', 'Light'], ['dark', 'Dark']];
const priorities = [['critical', 'Critical'], ['high', 'High'], ['medium', 'Medium'], ['low', 'Low'], ['maintenance', 'Maintenance']];

export function Settings({ title }) {
  const { theme, setTheme, accent, setAccent, resolved, priorityColours, setPriorityColour, squareCorners, setSquareCorners, cardShadows, setCardShadows, reset } = useSettings();
  const username = useAuth()?.user?.username;
  const [count, setCount] = useState(() => rememberedCount(username));
  const [forgotten, setForgotten] = useState(null);
  const { ranges, add, remove } = useSavedRanges();
  const [draft, setDraft] = useState({ name: '', amount: '3', unit: 'months' });
  const [rangeError, setRangeError] = useState('');
  const addRange = (e) => {
    e.preventDefault();
    const result = add(draft);
    setRangeError(result.ok ? '' : result.error);
    if (result.ok) setDraft({ ...draft, name: '' });
  };
  return (
    <>
      <PageHeader title={title} range={false} />
      <section class="panel">
        <h2>Appearance</h2>
        <div class="toolbar">
          <label class="field">Theme
            <select value={theme} onChange={(e) => setTheme(e.currentTarget.value)}>
              {themes.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
            </select>
          </label>
          <label class="field">Accent Colour
            <span class="select-with-dot">
              <span class="dot" style={{ background: accents[accent]?.[resolved] }} aria-hidden="true" />
              <select value={accent} onChange={(e) => setAccent(e.currentTarget.value)}>
                {Object.entries(accents).map(([k, a]) => <option key={k} value={k}>{a.label}</option>)}
              </select>
            </span>
          </label>
        </div>
        <fieldset class="group">
          <legend>Ticket Priority Row Colours</legend>
          <div class="choices">
            {priorities.map(([k, label]) => (
              <label key={k}>
                <input type="color" value={priorityColours[k]} onInput={(e) => setPriorityColour(k, e.target.value)} />{label}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset class="group">
          <legend>Layout</legend>
          <div class="choices">
            <label><input type="checkbox" checked={squareCorners} onChange={(e) => setSquareCorners(e.target.checked)} />Square corners</label>
            <label><input type="checkbox" checked={cardShadows} onChange={(e) => setCardShadows(e.target.checked)} />Card shadows</label>
          </div>
        </fieldset>
        <button class="btn" onClick={reset}>Reset to defaults</button>
      </section>
      <section class="panel">
        <h2>Saved Date Ranges</h2>
        <p class="note">Name a length of time, such as "last 3 months", and it appears as a quick button on the date range at the top, in a report's date choices, and in the trend's range box.
          Only the length is saved: the dates are worked out each time you use it, so it always ends today. They are kept in this browser, for your account only, and are not removed by forgetting remembered inputs.</p>
        {ranges.length === 0 ? <p class="note">You have not saved any ranges yet.</p> : (
          <div class="table-wrap"><table>
            <thead><tr><th>Name</th><th>Length</th><th /></tr></thead>
            <tbody>
              {ranges.map((r) => (
                <tr key={r.name}><td><strong>{r.name}</strong></td><td>{describeDuration(r)}</td>
                  <td class="row-actions"><button type="button" class="btn small" aria-label={`Remove ${r.name}`} onClick={() => remove(r.name)}>Remove</button></td></tr>
              ))}
            </tbody>
          </table></div>
        )}
        <form class="toolbar" onSubmit={addRange}>
          <label class="field">Name<input value={draft.name} maxLength={MAX_RANGE_NAME} placeholder="Board pack" onInput={(e) => setDraft({ ...draft, name: e.currentTarget.value })} /></label>
          <label class="field">Last<input type="number" min="1" max={DURATION_UNITS[draft.unit]} value={draft.amount} onInput={(e) => setDraft({ ...draft, amount: e.currentTarget.value })} /></label>
          <label class="field">Unit
            <select value={draft.unit} onChange={(e) => setDraft({ ...draft, unit: e.currentTarget.value })}>{Object.keys(DURATION_UNITS).map((u) => <option key={u} value={u}>{u}</option>)}</select>
          </label>
          <button class="btn" type="submit" disabled={ranges.length >= MAX_SAVED_RANGES}>Save Range</button>
        </form>
        {rangeError && <p class="note error" role="alert">{rangeError}</p>}
      </section>
      <section class="panel">
        <h2>Remembered Inputs</h2>
        <p class="note">Your last date range, filters, sorting and page sizes are remembered in this browser, for your account only, so you do not have to set them again.
          A date range that ended today is remembered as its length (for example the last 30 days). Forgetting them puts every page back to its starting point. Your saved date ranges (above) are kept.</p>
        <div class="toolbar">
          <button class="btn" disabled={count === 0} onClick={() => { setForgotten(forgetRemembered(username)); setCount(0); }}>Forget Remembered Inputs{count ? ` (${count})` : ''}</button>
          {forgotten != null && <span class="note" role="status">{forgotten} remembered value(s) forgotten. Pages already open keep what they show until you reload them.</span>}
        </div>
      </section>
    </>
  );
}
