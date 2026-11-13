import { useState } from 'preact/hooks';
import { isFilters } from '../persisted';
import { useDateRange } from '../state/DateRangeContext';
import { ticketTrend } from '../trend';
import { TrendChart } from './TrendChart';
import { usePersistentState } from '../state/persist';
import { worstTickets } from '../worst';
import { PageHeader } from './PageHeader';
import { Period } from './Period';
import { Donut } from './Donut';
import { BarChart } from './BarChart';
import { ChartCard, ChartControls } from './ChartCard';
import { DataAsOf } from './DataAsOf';
import { DetailModal, fact } from './DetailModal';
import { StatCard, TILE_COLS, DataTable, ErrorNote, FilterBubbles } from './ui';
import { useSection } from './useSection';
import { formatDuration } from '../text';

// Every chart a ticket page can show, keyed by name (`bar` = horizontal bars instead of a donut). A page picks and orders them.
// `requires` hides a chart, and shows `unavailable`, when the response does not say that data exists.
// Priority slices use the colours chosen in Settings.
export const DIM = {
  client: { key: 'company_name', label: 'Client', chart: 'Ticket By Client' },
  priority: { key: 'priority', label: 'Priority', chart: 'Ticket By Priority', colour: (v) => `var(--priority-${v.toLowerCase()}, #8e96b3)` },
  type: { key: 'ticket_type', label: 'Type', chart: 'Ticket By Type' },
  issue: { key: 'issue_type', label: 'Issue Type', chart: 'Ticket By Issue Type' },
  subtype: { key: 'sub_issue_type', label: 'Sub Type', chart: 'Ticket By Sub Type', bar: true },
  technician: {
    key: 'technician', label: 'Technician', chart: 'Ticket By Technician', requires: 'technician_available',
    unavailable: 'Technician data is not being collected yet: the Autotask collector does not capture who a ticket is assigned to, so there is nothing to chart or filter by. See docs/05_OPERATIONS.md (Technician data).',
  },
};
// Summary tiles that also work as filters.
const FLAGS = [
  { key: 'open', label: 'Open Tickets', test: (r) => !r.completed_at },
  { key: 'flagged', label: 'Flagged Tickets', test: (r) => !!r.rowStatus },
  { key: 'sla_missed', label: 'SLA Missed', test: (r) => r.sla_met === false },
];
const PALETTE = ['#4f8cff', '#2b2d9e', '#7a1f7a', '#d94fa3', '#e3703a', '#d4a017', '#c9504f', '#3a8f5c'];

const flag = (key) => FLAGS.find((f) => f.key === key);
const matches = (rows, filters) => rows.filter((r) => Object.entries(filters).every(([k, v]) => (flag(k) ? flag(k).test(r) : r[k] === v)));
const count = (rows, test) => rows.filter(test).length;
const countBy = (rows, key) => {
  const m = new Map();
  for (const r of rows) m.set(r[key], (m.get(r[key]) ?? 0) + 1);
  return [...m].map(([label, n]) => ({ label, count: n })).sort((a, b) => b.count - a.count);
};
const avg = (rows, key) => {
  const v = rows.map((r) => r[key]).filter((x) => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};

const ticketFacts = (t) => [
  fact(t, 'Client', 'company_name'), fact(t, 'Technician', 'technician'), fact(t, 'Type', 'ticket_type'),
  fact(t, 'Priority', 'priority', 'priority'), fact(t, 'Status', 'status'), fact(t, 'Issue Type', 'issue_type'),
  fact(t, 'Sub Type', 'sub_issue_type'), fact(t, 'Created', 'created_at', 'datetime'),
  fact(t, 'First Response', 'first_response_at', 'datetime'), fact(t, 'Completed', 'completed_at', 'datetime'),
  fact(t, 'Response Time', 'response_hours', 'duration'), fact(t, 'Resolution Time', 'resolution_hours', 'duration'),
  fact(t, 'SLA Met', 'sla_met', 'bool'), fact(t, 'Response Met', 'first_response_met', 'bool'),
  fact(t, 'Ticket Id', 'ticket_id'), fact(t, 'Company Id', 'company_id'), fact(t, 'Snapshot', 'snapshot_id'),
];

// One request returns every ticket for the period. Filters, charts, tiles, search and sorting are all computed
// here, so clicking a slice, a tile or a bubble never makes another request.
// The page starts worst first (late or flagged, then the longest open); the filters are remembered between visits under `persistKey`.
export function TicketExplorer({ title, dimensions, persistKey }) {
  const [savedFilters, setFilters] = usePersistentState(persistKey ? `${persistKey}:filters` : null, {}, isFilters);
  const [ticket, setTicket] = useState(null);
  const { data, error } = useSection('tickets');
  const { applied } = useDateRange();   // the period the tickets were fetched for

  // Same value again (or empty) removes the filter; a different value replaces it.
  const toggle = (key, value) => setFilters((f) => {
    const { [key]: current, ...rest } = f;
    return value && current !== value ? { ...rest, [key]: value } : rest;
  });

  const shown = dimensions.filter((d) => !d.requires || data?.[d.requires]);   // charts whose data exists
  const unavailable = data ? dimensions.filter((d) => d.requires && !data[d.requires]) : [];
  // A remembered filter on something this page no longer offers (or never had) is ignored.
  const filters = Object.fromEntries(Object.entries(savedFilters).filter(([k]) => flag(k) || shown.some((d) => d.key === k)));
  const all = data?.tickets.rows ?? [];
  const rows = worstTickets(matches(all, filters));
  const trend = ticketTrend(rows, applied.from, applied.to);   // tickets created per day, or per week for a long period
  const flagged = count(rows, flag('flagged').test);
  const missed = count(rows, flag('sla_missed').test);
  const tiles = [
    { label: 'Ticket Count', value: rows.length },
    { label: 'Open', value: count(rows, flag('open').test), filter: 'open' },
    { label: 'Closed', value: count(rows, (r) => !!r.completed_at) },
    { label: 'Flagged Tickets', value: flagged, status: flagged ? 'bad' : 'good', filter: 'flagged' },
    { label: 'SLA Missed', value: missed, status: missed ? 'bad' : 'good', filter: 'sla_missed' },
    { label: 'Avg Resolution Time', value: formatDuration(avg(rows, 'resolution_hours')) },
    { label: 'Avg Response Time', value: formatDuration(avg(rows, 'response_hours')) },
  ];
  const bubbles = [
    ...shown.filter((d) => filters[d.key]).map((d) => ({ label: d.label, value: filters[d.key], onRemove: () => toggle(d.key, null) })),
    ...FLAGS.filter((f) => filters[f.key]).map((f) => ({ label: f.label, onRemove: () => toggle(f.key, null) })),
  ];

  return (
    <>
      <PageHeader title={title} />
      <DataAsOf source="tickets" />
      <ErrorNote error={error} />
      {unavailable.map((d) => <p key={d.key} class="note" role="status">{d.unavailable}</p>)}
      {data && (
        <>
          <div class="row">
            {tiles.map(({ filter, ...t }) => (
              <StatCard key={t.label} {...t} active={filter ? !!filters[filter] : undefined} onClick={filter ? () => toggle(filter, true) : undefined} cols={TILE_COLS} />
            ))}
          </div>
          <ChartControls>
          <div class="row">
            {shown.map((d) => {
              const labels = countBy(all, d.key).map((s) => s.label); // stable order and colours across filters
              const colorOf = d.colour ?? ((l) => PALETTE[labels.indexOf(l) % PALETTE.length]);
              const counts = Object.fromEntries(countBy(rows, d.key).map((s) => [s.label, s.count]));
              const Chart = d.bar ? BarChart : Donut;
              return <Chart key={d.key} title={d.chart} labels={labels} counts={counts} selected={filters[d.key]}
                onSelect={(v) => toggle(d.key, v)} colorOf={colorOf} />;
            })}
          </div>
          <ChartCard title={trend.heading} total={rows.length}><TrendChart {...trend} showHeading={false} /></ChartCard>
          </ChartControls>
          <DataTable {...data.tickets} persistKey={persistKey} rows={rows} onRowClick={setTicket}
            above={<><Period /><FilterBubbles items={bubbles} /></>} />
        </>
      )}
      {ticket && (
        <DetailModal kicker={ticket.ticket_number} title={ticket.title} banner={ticket.reason && `⚑ ${ticket.reason}`}
          facts={ticketFacts(ticket)} blocks={[['Description', ticket.description], ['Resolution', ticket.resolution]]}
          onClose={() => setTicket(null)} />
      )}
    </>
  );
}
