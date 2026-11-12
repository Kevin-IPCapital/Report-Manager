import { useEffect, useState } from 'preact/hooks';
import { ChartCard } from './ChartCard';

// Horizontal bars with the same contract as Donut: stable `labels`, `counts` for the current filter. Bars grow and
// shrink with the counts, and rows outside the filter collapse away. Click a row to filter by it.
export function BarChart({ title, labels, counts, selected, onSelect, colorOf, unit = 'tickets' }) {
  const [ready, setReady] = useState(false);
  useEffect(() => { const id = setTimeout(() => setReady(true), 30); return () => clearTimeout(id); }, []);

  const n = (label) => counts[label] ?? 0;
  const total = labels.reduce((sum, l) => sum + n(l), 0);
  const max = Math.max(1, ...labels.map(n));
  return (
    <ChartCard title={title} total={total} unit={unit}>
      <div class="bars">
        {labels.map((label) => (
          <button key={label} class={`bar-row ${n(label) ? '' : 'zero'} ${selected === label ? 'on' : ''}`} aria-pressed={selected === label}
            aria-hidden={!n(label)} tabIndex={n(label) ? 0 : -1} onClick={() => onSelect(label)}>
            <span class="bar-label">{label}</span>
            <span class="bar-track"><span class="bar-fill" style={{ width: ready ? `${(n(label) / max) * 100}%` : '0%', background: colorOf(label) }} /></span>
            <span class="bar-value">{n(label)} ({total ? ((n(label) / total) * 100).toFixed(2) : '0.00'}%)</span>
          </button>
        ))}
      </div>
    </ChartCard>
  );
}
