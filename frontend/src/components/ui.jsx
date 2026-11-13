import { useEffect, useState } from 'preact/hooks';
import { columnKind, matchesQuery, searchHint } from '../filter';
import { define, hint } from '../glossary';
import { isPageSize, isSort, usableSort } from '../persisted';
import { DEFAULT_PAGE_SIZE, paginate } from '../pagination';
import { usePersistentState } from '../state/persist';
import { Pager } from './Pager';
import { formatBytes, formatDuration, titleCase } from '../text';

const STATUS_LABELS = { good: 'On target', warn: 'Attention', bad: 'Action needed' };

export const StatusBadge = ({ status }) => <span class={`badge ${status}`}>{STATUS_LABELS[status] || status}</span>;

// With onClick the tile is a toggle button (used to filter); `active` shows that it is applied.
// `scope` picks the page's own definition of a label ("Servers" means one thing on Server Health and another on Backups).
// A row of four tiles on a wide page, two on a medium one, one on a phone: the classes that make that, for a StatCard inside a `.row`.
export const TILE_COLS = 'col-12 col-sm-6 col-lg-3';
// The same for a page with five or six tiles, which sit better three across than four (3 + 2, or 3 + 3).
export const TILE_COLS_THREE = 'col-12 col-sm-6 col-lg-4';

export function StatCard({ label, value, status, onClick, active, scope, cols = '' }) {
  const body = (<><div class="stat-value">{value}</div><div {...hint(label, 'stat-label', scope)}>{titleCase(label)}</div></>);
  return onClick
    ? <button type="button" class={`stat clickable ${status || ''} ${active ? 'active' : ''} ${cols}`.trim()} aria-pressed={!!active} onClick={onClick}>{body}</button>
    : <div class={`stat ${status || ''} ${cols}`.trim()}>{body}</div>;
}

const HEALTH_TONE = { OK: 'good', WARNING: 'warn', CRITICAL: 'bad' };
const healthLabel = (v) => (String(v).toUpperCase() === 'OK' ? 'OK' : String(v).charAt(0).toUpperCase() + String(v).slice(1).toLowerCase());

const dateFmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const PRIORITY_RANK = { critical: 0, high: 1, medium: 2, low: 3, maintenance: 4 };

// How a value is displayed, by column type. `invert` makes a true bool show as bad (e.g. "Missing").
export const cell = (c, v) => {
  switch (c.type) {
    case 'status': return <StatusBadge status={v} />;
    case 'datetime': return v ? dateFmt.format(new Date(v)) : '—';
    case 'duration': return formatDuration(v);
    case 'bytes': return formatBytes(v);
    case 'health': return v ? <span class={`badge ${HEALTH_TONE[String(v).toUpperCase()] || ''}`}>{healthLabel(v)}</span> : '—';
    case 'percent': return v == null ? '—' : `${v.toFixed(2)}%`;
    case 'bool': return v == null ? '—'
      : <span class={`tick ${(c.invert ? !v : v) ? 'yes' : 'no'}`} role="img" aria-label={v ? 'Yes' : 'No'}>{v ? '✓' : '✗'}</span>;
    case 'flag': return v ? <span class="tick" role="img" aria-label="Yes">✓</span> : '—';
    case 'priority': return v ? <span class={`pri pri-${String(v).toLowerCase()}`}>{v}</span> : '—';
    case 'link': return <button class="rowlink">{v}</button>; // the click is handled by the row
    default: return v ?? '';
  }
};

// The text a column search matches against: what the user sees, not the raw value.
const searchText = (c, v) => {
  switch (c.type) {
    case 'status': return STATUS_LABELS[v] || String(v ?? '');
    case 'datetime': return v ? dateFmt.format(new Date(v)) : '';
    case 'duration': return v == null ? '' : formatDuration(v);
    case 'bytes': return v == null ? '' : formatBytes(v);
    case 'health': return v ? healthLabel(v) : '';
    case 'percent': return v == null ? '' : `${v.toFixed(2)}%`;
    case 'bool': case 'flag': return v == null ? '' : v ? 'yes' : 'no';
    default: return v == null ? '' : String(v);
  }
};

const isEmpty = (v) => v == null || v === '';
const sortKey = (c, v) => (c.type === 'priority' ? PRIORITY_RANK[String(v).toLowerCase()] ?? 99 : v);

// Table with, per column: click the name to search that column, the caret to sort (ascending, descending,
// off), and the dash to collapse it. `above` is shown between the title and the table (period, bubbles).
// `persistKey` names the table so its sort and page size are remembered between visits; without it nothing is remembered. The rows are
// shown in the order they are given (each page gives them worst first where it defines that), and "Reset Sort" returns to it.
export function DataTable({ title, columns, rows, onRowClick, above, persistKey }) {
  const [savedSort, setSort] = usePersistentState(persistKey ? `table:${persistKey}:sort` : null, null, isSort);
  const sort = usableSort(savedSort, columns);   // a remembered sort on a column that has gone is ignored
  const [query, setQuery] = useState({});
  const [editing, setEditing] = useState(null); // the column whose heading is currently a search box
  const [hidden, setHidden] = useState([]);     // keys of collapsed columns
  const [page, setPage] = useState(0);
  const [size, setSize] = usePersistentState(persistKey ? `table:${persistKey}:size` : null, DEFAULT_PAGE_SIZE, isPageSize());
  useEffect(() => setPage(0), [rows.length]);   // a different set of rows starts again from the first page

  const byKey = Object.fromEntries(columns.map((c) => [c.key, c]));
  const needles = Object.entries(query).filter(([k, q]) => q && byKey[k]);
  const kinds = {};   // 'number', 'date' or 'text' per column, worked out only when needed
  const kindOf = (c) => (kinds[c.key] ??= columnKind(c, rows));
  const visible = rows.filter((r) => needles.every(([k, q]) => matchesQuery(byKey[k], kindOf(byKey[k]), r[k], searchText(byKey[k], r[k]), q)));
  const column = sort && byKey[sort.key];
  const shown = column ? [...visible].sort((a, b) => {
    const x = sortKey(column, a[column.key]), y = sortKey(column, b[column.key]);
    if (isEmpty(x) || isEmpty(y)) return isEmpty(x) === isEmpty(y) ? 0 : isEmpty(x) ? 1 : -1; // empty values stay last
    const diff = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true });
    return diff * sort.dir;
  }) : visible;
  const toggleSort = (key) => { setPage(0); setSort((s) => (!s || s.key !== key ? { key, dir: 1 } : s.dir === 1 ? { key, dir: -1 } : null)); };
  const focusable = onRowClick && !columns.some((c) => c.type === 'link'); // link columns already hold a button
  const view = paginate(shown, page, size);

  return (
    <section class="panel">
      <div class="panel-head">
        <h2>{titleCase(title)}</h2>
        <div class="panel-actions">
          {needles.length > 0 && <button class="btn small" onClick={() => { setQuery({}); setPage(0); }}>Clear Search</button>}
          {sort && <button class="btn small" title="Back to the order this page starts in (worst first where the page defines it)" onClick={() => { setSort(null); setPage(0); }}>Reset Sort</button>}
          {hidden.length > 0 && <button class="btn small" onClick={() => setHidden([])}>Show Columns ({hidden.length})</button>}
          <span class="count">{visible.length === rows.length ? rows.length : `${visible.length} of ${rows.length}`} {rows.length === 1 ? 'row' : 'rows'}</span>
        </div>
      </div>
      {above && <div class="table-above">{above}</div>}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              {columns.map((c) => {
                const on = sort?.key === c.key;
                const label = titleCase(c.label);
                return (
                  <th key={c.key} class={hidden.includes(c.key) ? 'col-collapsed' : undefined}
                    aria-sort={on ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined}>
                    {hidden.includes(c.key) ? (
                      <button class="th-btn" title={label} aria-label={`Expand column ${label}`} onClick={() => setHidden(hidden.filter((k) => k !== c.key))}>▸</button>
                    ) : editing === c.key ? (
                      <input class="th-search" placeholder={searchHint(kindOf(c), c, label)} title={searchHint(kindOf(c), c, label)} value={query[c.key] ?? ''} ref={(el) => el && el.focus()}
                        onInput={(e) => { setQuery({ ...query, [c.key]: e.target.value }); setPage(0); }} onBlur={() => setEditing(null)}
                        onKeyDown={(e) => (e.key === 'Enter' || e.key === 'Escape') && setEditing(null)} />
                    ) : (
                      <span class="th-inner">
                        <button class="th-label" title={`${define(c.label) ? define(c.label) + ' ' : ''}Click to search this column.`} onClick={() => setEditing(c.key)}>
                          {label}{query[c.key] && <span class="th-query">“{query[c.key]}”</span>}
                        </button>
                        <button class="th-btn" aria-label={`Sort by ${label}`} onClick={() => toggleSort(c.key)}>{on ? (sort.dir === 1 ? '▲' : '▼') : '⇅'}</button>
                        <button class="th-btn th-collapse" aria-label={`Collapse column ${label}`} onClick={() => setHidden([...hidden, c.key])}>–</button>
                      </span>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {view.items.map((row, i) => (
              <tr key={i} onClick={onRowClick && (() => onRowClick(row))} title={row.reason || undefined}
                tabIndex={focusable ? 0 : undefined} onKeyDown={focusable ? (e) => e.key === 'Enter' && onRowClick(row) : undefined}
                class={[row.rowStatus && `row-${row.rowStatus}`, row.rowTone && `tone-${row.rowTone}`, onRowClick && 'clickable'].filter(Boolean).join(' ') || undefined}>
                {columns.map((c) => (hidden.includes(c.key)
                  ? <td key={c.key} class="col-collapsed" />
                  : <td key={c.key} class={c.wrap ? 'wrap' : undefined}>{cell(c, row[c.key])}</td>))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager view={view} size={size} onSize={(n) => { setSize(n); setPage(0); }} onPage={setPage} />
    </section>
  );
}

export const SectionBody = ({ data }) => (
  <>
    <div class="stats">{data.stats.map((s) => <StatCard key={s.label} {...s} />)}</div>
    {data.tables.map((t) => <DataTable key={t.title} {...t} />)}
  </>
);

export const ErrorNote = ({ error }) =>
  error ? <p class="note error" role="alert">Couldn’t load this section: {error.message}</p> : null;

// Active filters as removable bubbles; clicking a bubble removes that filter. `value` is optional.
export const FilterBubbles = ({ items }) => (items.length ? (
  <div class="bubbles" role="group" aria-label="Active filters">
    {items.map((f) => (
      <button key={`${f.label}:${f.value ?? ''}`} class="bubble" onClick={f.onRemove} aria-label={`Remove filter: ${f.label}${f.value != null ? ` ${f.value}` : ''}`}>
        {f.label}{f.value != null && <>: <strong>{f.value}</strong></>}
      </button>
    ))}
  </div>
) : null);

export const LoadingOverlay = () => (
  <div class="overlay" role="status">
    <div class="spinner" aria-hidden="true" />
    <span class="sr-only">Loading</span>
  </div>
);
