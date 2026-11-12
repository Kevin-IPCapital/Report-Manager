import { useState } from 'preact/hooks';
import { FILTER_DROPDOWN_LIMIT, MAX_FILTER_VALUES, filterValues } from '../report/config';
import { FilterBubbles } from './ui';

// A report filter that can hold several values: each chosen value is a bubble that is removed by clicking it, and a box adds another. A
// row matches if it has any of the chosen values. `options` are text, or { value, label } when what is stored differs from what is shown.
// `max` is how many can be chosen (20 for a report filter). With `restrict` only the options can be chosen (a person's client groups, a group's clients):
// nothing typed is accepted unless it is one of them, and with none to choose from `noneText` is shown. `emptyLabel` is the blank choice's wording.
export function MultiFilter({ label, value, options = [], onChange, loading, max = MAX_FILTER_VALUES, emptyLabel = 'All (choose to filter)', restrict = false, noneText = 'Nothing to choose from yet.' }) {
  const chosen = filterValues(value);
  const [typed, setTyped] = useState('');
  const choices = options.map((o) => (typeof o === 'string' ? { value: o, label: o } : o));
  const labelOf = (v) => choices.find((c) => c.value === v)?.label ?? v;
  const has = (v) => chosen.some((x) => x.toLowerCase() === v.toLowerCase());
  const full = chosen.length >= max;
  const add = (v) => {
    let text = String(v ?? '').trim();
    if (restrict) text = choices.find((c) => c.value.toLowerCase() === text.toLowerCase() || c.label.toLowerCase() === text.toLowerCase())?.value ?? '';
    if (!text || full || has(text)) return;
    onChange([...chosen, text]);
    setTyped('');
  };
  const remove = (v) => onChange(chosen.filter((x) => x !== v));
  const listId = `multi-${label.replace(/\W/g, '-')}`;

  return (
    <div class="field multi" role="group" aria-label={label}>
      <span>{label}</span>
      <FilterBubbles items={chosen.map((v) => ({ label, value: labelOf(v), onRemove: () => remove(v) }))} />
      {loading ? <input disabled placeholder="Loading options…" /> : choices.length > 0 && choices.length <= FILTER_DROPDOWN_LIMIT ? (
        <select value="" disabled={full} aria-label={`Add ${label}`} onChange={(e) => add(e.currentTarget.value)}>
          <option value="">{chosen.length ? 'Add another…' : emptyLabel}</option>
          {choices.filter((c) => !has(c.value)).map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select>
      ) : restrict && choices.length === 0 ? <span class="note">{noneText}</span> : (
        <div class="multi-add">
          <input list={listId} value={typed} disabled={full} aria-label={`Add ${label}`} placeholder={choices.length ? `Search ${choices.length} values` : 'Type a value, then Add'}
            onInput={(e) => setTyped(e.currentTarget.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(typed); } }} />
          {choices.length > FILTER_DROPDOWN_LIMIT && <datalist id={listId}>{choices.filter((c) => !has(c.value)).map((c) => <option key={c.value} value={c.value} />)}</datalist>}
          <button type="button" class="btn small" disabled={full || !typed.trim()} onClick={() => add(typed)}>Add</button>
        </div>
      )}
      {full && <span class="note">At most {max} values.</span>}
    </div>
  );
}
