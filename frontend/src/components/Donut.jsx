import { useEffect, useState } from 'preact/hooks';
import { ChartCard } from './ChartCard';

const R = 40;
const C = 2 * Math.PI * R;

// Donut with a clickable legend. `labels` is the stable list of every possible slice (so slices keep their
// place and colour); `counts` maps label -> number for the CURRENT filter. When the counts change, each
// slice animates to its new length and position; slices outside the filter shrink away.
export function Donut({ title, labels, counts, selected, onSelect, colorOf, unit = 'tickets' }) {
  const [ready, setReady] = useState(false); // draws empty first so the initial load animates in too
  useEffect(() => { const id = setTimeout(() => setReady(true), 30); return () => clearTimeout(id); }, []);

  const n = (label) => counts[label] ?? 0;
  const total = labels.reduce((sum, l) => sum + n(l), 0);
  const shown = labels.filter((l) => n(l) > 0);
  const gap = shown.length > 1 ? 1 : 0;
  let offset = 0;
  return (
    <ChartCard title={title} total={total} unit={unit}>
      <div class="donut-row">
        <svg viewBox="0 0 120 120" class="donut" role="img" aria-label={`${title}: ${total} ${unit}`}>
          <circle cx="60" cy="60" r={R} class="donut-empty" />
          {labels.map((label) => {
            const len = ready && total ? (n(label) / total) * C : 0;
            const seg = (
              <circle key={label} cx="60" cy="60" r={R} class="donut-seg" onClick={() => onSelect(label)}
                style={{ stroke: colorOf(label), strokeDasharray: `${Math.max(len - (n(label) ? gap : 0), 0)} ${C}`, strokeDashoffset: -offset }} />
            );
            offset += len;
            return seg;
          })}
          <text x="60" y="60" class="donut-total" text-anchor="middle" dominant-baseline="central">{total}</text>
        </svg>
        <ul class="legend">
          {shown.map((label) => (
            <li key={label}>
              <button class={selected === label ? 'on' : ''} aria-pressed={selected === label} onClick={() => onSelect(label)}>
                <i style={{ background: colorOf(label) }} />{label}
                <span>{n(label)} ({((n(label) / total) * 100).toFixed(2)}%)</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </ChartCard>
  );
}
