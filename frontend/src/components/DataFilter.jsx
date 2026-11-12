// A single-choice filter where each choice has a value and a label (the client a patch section is for).
export function ChoiceFilter({ label, value, choices, onChange, loading }) {
  return (
    <label class="field">{label}
      <select value={value} disabled={loading} onChange={(e) => onChange(e.currentTarget.value)}>
        <option value="">{loading ? 'Loading options…' : 'All'}</option>
        {value && !choices.some((c) => c.value === value) && <option value={value}>{value} (not in current results)</option>}
        {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
    </label>
  );
}
