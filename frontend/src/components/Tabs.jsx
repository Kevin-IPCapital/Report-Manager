// Tabs: a row of buttons that switch between panels. Reached with the Tab key as one stop; the arrow keys, Home and End move between them, as in any set of tabs.
// The page draws the panel itself, with tabPanelProps(prefix, key) so that each tab and its panel point at one another for a screen reader.
export const tabPanelProps = (prefix, key) => ({ role: 'tabpanel', id: `${prefix}-panel-${key}`, 'aria-labelledby': `${prefix}-tab-${key}` });

export function Tabs({ tabs, value, onChange, label, prefix = 'tabs' }) {
  const move = (e, i) => {
    const last = tabs.length - 1;
    const next = { ArrowRight: i === last ? 0 : i + 1, ArrowLeft: i === 0 ? last : i - 1, Home: 0, End: last }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    onChange(tabs[next][0]);
    document.getElementById(`${prefix}-tab-${tabs[next][0]}`)?.focus();
  };
  return (
    <div class="tabs" role="tablist" aria-label={label}>
      {tabs.map(([key, text], i) => (
        <button type="button" role="tab" key={key} id={`${prefix}-tab-${key}`} aria-selected={value === key} aria-controls={`${prefix}-panel-${key}`} tabIndex={value === key ? 0 : -1}
          class={value === key ? 'active' : ''} onClick={() => onChange(key)} onKeyDown={(e) => move(e, i)}>{text}</button>
      ))}
    </div>
  );
}
