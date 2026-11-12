import { createContext } from 'preact';
import { useContext, useEffect, useState } from 'preact/hooks';
import { hint } from '../glossary';
import { titleCase } from '../text';

// `n` counts the commands sent, so sending "collapse" twice (after opening one card by hand) still reaches every card.
const Command = createContext({ open: true, n: 0 });

// Wraps a group of ChartCards and adds "Collapse All" / "Expand All". Collapsing hides the charts only: filters stay applied.
export function ChartControls({ children }) {
  const [command, setCommand] = useState({ open: true, n: 0 });
  const send = (open) => setCommand((c) => ({ open, n: c.n + 1 }));
  return (
    <Command.Provider value={command}>
      <div class="toolbar">
        <button class="btn small" onClick={() => send(false)}>Collapse All Charts</button>
        <button class="btn small" onClick={() => send(true)}>Expand All Charts</button>
      </div>
      {children}
    </Command.Provider>
  );
}

// Card with a title, a count (left out when `total` is not given) and a minimise button; the body is hidden (not unmounted) while minimised.
export function ChartCard({ title, total, unit = 'tickets', children }) {
  const [open, setOpen] = useState(true);
  const command = useContext(Command);
  useEffect(() => { if (command.n) setOpen(command.open); }, [command.n]);
  return (
    <section class="panel chart">
      <div class="panel-head">
        <h2 {...hint(title)}>{titleCase(title)}</h2>
        <div class="panel-actions">
          {total != null && <span class="count">{total} {unit}</span>}
          <button class="icon-btn small" aria-expanded={open} aria-label={`${open ? 'Minimise' : 'Expand'} ${title}`} onClick={() => setOpen(!open)}>
            {open ? '–' : '+'}
          </button>
        </div>
      </div>
      <div class="chart-body" hidden={!open}>{children}</div>
    </section>
  );
}
