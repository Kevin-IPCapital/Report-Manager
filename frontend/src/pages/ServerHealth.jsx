import { useEffect, useState } from 'preact/hooks';
import { api } from '../api/adapter';
import { PageHeader } from '../components/PageHeader';
import { DataAsOf } from '../components/DataAsOf';
import { DeviceFilter } from '../components/DeviceFilter';
import { Pager } from '../components/Pager';
import { StatCard, ErrorNote, FilterBubbles, cell } from '../components/ui';
import { useApi } from '../components/useApi';
import { hint } from '../glossary';
import { isFilters, isOneOf, isPageSize, isText } from '../persisted';
import { paginate } from '../pagination';
import { usePersistentState } from '../state/persist';
import { SERVER_SORTS, SERVER_SORT_KEYS, sortServers } from '../worst';
import { CONNECTION_TONE, TILE_FILTERS, applyFilters, collectedView, connectionOf, ncentralStatus, patchView, tileCounts, toggleFilter } from '../serverHealth';
import { formatDuration, titleCase } from '../text';

const CARD_SIZES = [12, 24, 48, 96];
// Remembered tile filters must be real ones, switched on, and not the contradictory pair.
const isTileFilters = (v) => isFilters(v) && Object.entries(v).every(([k, on]) => TILE_FILTERS[k] && on === true) && !(v.connected && v.disconnected) && !(v.ncConnected && v.ncDisconnected);

// One section of a card. It opens and closes on its own heading; "Expand All" and "Collapse All" above the cards set every section at once
// (`signal` changes when one is pressed), and a card that appears later on another page starts as the last one pressed left things.
function CardSection({ title, badge, signal, children }) {
  const [open, setOpen] = useState(signal.open);
  useEffect(() => { setOpen(signal.open); }, [signal.version]);
  return (
    <details class="card-section" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary><span {...hint(title)}>{title}</span>{badge}</summary>
      <div class="card-section-body">{children}</div>
    </details>
  );
}

// Performance: no source supplies these to the dashboard yet.
function PerformanceFacts() {
  return (
    <dl class="facts perf">
      <div><dt {...hint('CPU')}>CPU</dt><dd class="muted">No data</dd></div>
      <div><dt {...hint('RAM')}>RAM</dt><dd class="muted">No data</dd></div>
      <div><dt {...hint('Disk')}>Disk</dt><dd class="muted">No data</dd></div>
    </dl>
  );
}

// Patches: a table with a row for each of the three categories: when a patch was last added, when the next is due, and an Up To Date tick
// (nothing is missing). A device N-central cannot find says so above the table. There is no overall verdict: what "healthy" means is not decided.
function PatchTable({ view }) {
  if (!view.matched) return <p class="note">{view.note}</p>;
  return (
    <>
      {view.collection.note && <p class={`note patch-collection ${view.collection.tone}`} role="status">{view.collection.note}</p>}
      <div class="table-wrap">
        <table class="patch-table">
          <thead>
            <tr><th>Category</th><th {...hint('Last Added')}>Last Added</th><th {...hint('Next Due')}>Next Due</th><th {...hint('Patches Up To Date')}>Up To Date</th></tr>
          </thead>
          <tbody>
            {view.rows.map((r) => (
              <tr key={r.key}>
                <td>{r.label}</td>
                <td>{r.lastDate}{r.lastAgo && <><br /><span class="muted">{r.lastAgo}</span></>}</td>
                <td class="muted" title={r.nextTitle}>{r.next}</td>
                <td title={r.upTitle}>
                  {r.upToDate == null ? <span class="muted">—</span> : <span class={`patch-tick ${r.upToDate ? 'on' : 'off'}`} role="checkbox" aria-checked={r.upToDate} aria-readonly="true" aria-label={`${r.label}: up to date`} />}
                  {r.flag && <span class={`patch-flag ${r.flag}`}>{r.flag === 'overdue' ? '⚠ ' : ''}{r.flagText}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// Data Collected: the latest date found for this device on a successful run of each collector.
function CollectedTable({ collected }) {
  const rows = collectedView(collected);
  if (!rows.length) return <p class="note">Nothing is known about when this server's data was collected.</p>;
  return (
    <div class="table-wrap">
      <table class="collected-table">
        <thead><tr><th>Collector</th><th>Data</th><th>Latest Date Found</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}><td>{r.source}</td><td>{r.what}</td><td class={r.found ? '' : 'muted'} title={r.title || undefined}>{r.date}{r.ago && <><br /><span class="muted">{r.ago}</span></>}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ServerCard({ s, grace, signal }) {
  const patches = patchView(s.patches, grace);
  const nc = ncentralStatus(s.patches);
  const flag = patches.matched && patches.collection.label ? <span class={`badge ${patches.collection.tone}`} title={patches.collection.note}>{patches.collection.label}</span> : null;
  return (
    <article class="panel agent-card">
      <h2>{s.computer_name}</h2>
      {s.customer_name && <p class="note">{s.customer_name}</p>}
      <dl class="facts">
        <div><dt {...hint('SentinelOne Status')}>SentinelOne Status</dt><dd><span class={`badge ${CONNECTION_TONE[connectionOf(s.network_status)]}`}>{titleCase(s.network_status) || '—'}</span></dd></div>
        <div><dt {...hint('N-central Status')}>N-central Status</dt><dd><span class={`badge ${nc.tone}`} title={nc.title}>{nc.label}</span></dd></div>
        <div><dt {...hint('Data Age')}>Data Age</dt><dd title={s.last_collected_at ? `Latest collected: ${new Date(s.last_collected_at).toLocaleString()}` : 'Collection timestamp unavailable'}>{formatDuration(s.data_age_hours)}</dd></div>
        <div><dt {...hint('Machine Type')}>Machine Type</dt><dd>{titleCase(s.machine_type) || '—'}</dd></div>
        <div><dt {...hint('OS')}>OS</dt><dd>{titleCase(s.os_type) || '—'}</dd></div>
        <div><dt {...hint('Agent Version')}>Agent Version</dt><dd>{s.agent_version || '—'}</dd></div>
        <div><dt {...hint('Mitigation Mode')}>Mitigation Mode</dt><dd>{titleCase(s.mitigation_mode) || '—'}</dd></div>
        <div><dt {...hint('Up To Date')}>Up To Date</dt><dd>{cell({ type: 'bool' }, s.is_up_to_date)}</dd></div>
        <div><dt {...hint('Clean')}>Clean</dt><dd>{cell({ type: 'bool' }, s.infected == null ? null : !s.infected)}</dd></div>
      </dl>
      <CardSection title="Performance" signal={signal}><PerformanceFacts /></CardSection>
      <CardSection title="Patches" badge={flag} signal={signal}><PatchTable view={patches} /></CardSection>
      <CardSection title="Data Collected" signal={signal}><CollectedTable collected={s.collected} /></CardSection>
    </article>
  );
}

// SentinelOne endpoints, one card each. The tiles count what is shown and, except the first, filter when clicked; the device box narrows by name;
// the cards are paged and start worst first (infected, then not connected, then the oldest data), or in the order chosen in the Sort box, and all
// of those choices are remembered between visits. Each card has three sections that open on their own headings (Expand All and Collapse All set
// them all): Performance (CPU, RAM and disk: placeholders until a source exists), Patches (a table of the three Overview categories, from the
// N-central device with the same name in the same client, with a Disconnected badge when N-central cannot find that device) and Data Collected
// (the latest date found for the device on a successful run of each collector).
export function ServerHealth({ title }) {
  const { data, error } = useApi(() => api.getSection('server-health'), []);
  const [active, setActive] = usePersistentState('servers:filters', {}, isTileFilters);     // which tile filters are on
  const [device, setDevice] = usePersistentState('servers:device', '', isText);
  const [sortKey, setSortKey] = usePersistentState('servers:sort', 'worst', isOneOf(SERVER_SORT_KEYS));
  const [size, setSize] = usePersistentState('servers:size', CARD_SIZES[1], isPageSize(CARD_SIZES));
  const [page, setPage] = useState(0);
  const [signal, setSignal] = useState({ open: false, version: 0 });   // Expand All / Collapse All: every card section follows the last one pressed

  const all = data?.servers ?? [];
  const rows = sortServers(applyFilters(all, active, device), sortKey);
  const counts = tileCounts(rows);
  const view = paginate(rows, page, size);
  useEffect(() => setPage(0), [rows.length]);   // a different set of cards starts again from the first page

  const toggle = (key) => { setActive((a) => toggleFilter(a, key)); setPage(0); };
  const tiles = [
    { label: 'Endpoints', value: counts.endpoints },
    { label: 'Servers', value: counts.servers, key: 'servers' },
    { label: 'SentinelOne Connected', value: counts.connected, status: 'good', key: 'connected' },
    { label: 'SentinelOne Disconnected', value: counts.disconnected, status: counts.disconnected ? 'bad' : 'good', key: 'disconnected' },
    { label: 'N-central Connected', value: counts.ncConnected, status: 'good', key: 'ncConnected' },
    { label: 'N-central Disconnected', value: counts.ncDisconnected, status: counts.ncDisconnected ? 'bad' : 'good', key: 'ncDisconnected' },
    { label: 'Infected', value: counts.infected, status: counts.infected ? 'bad' : 'good', key: 'infected' },
    { label: 'Out Of Date', value: counts.outdated, status: counts.outdated ? 'warn' : 'good', key: 'outdated' },
  ];
  const bubbles = [
    ...Object.keys(active).map((key) => ({ label: 'Show', value: TILE_FILTERS[key].label, onRemove: () => toggle(key) })),
    ...(device.trim() ? [{ label: 'Device', value: device.trim(), onRemove: () => setDevice('') }] : []),
  ];

  return (
    <>
      <PageHeader title={title} range={false} />
      <DataAsOf source="servers" />
      <ErrorNote error={error} />
      {data && (
        <>
          <div class="toolbar">
            <DeviceFilter value={device} onChange={(v) => { setDevice(v); setPage(0); }} names={all.map((s) => s.computer_name)} />
            <label class="field">Sort
              <select value={sortKey} onChange={(e) => { setSortKey(e.currentTarget.value); setPage(0); }}>
                {SERVER_SORTS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
              </select>
            </label>
            <div class="panel-actions" role="group" aria-label="Card sections">
              <button type="button" class="btn small" onClick={() => setSignal((x) => ({ open: true, version: x.version + 1 }))}>Expand All</button>
              <button type="button" class="btn small" onClick={() => setSignal((x) => ({ open: false, version: x.version + 1 }))}>Collapse All</button>
            </div>
          </div>
          <div class="stats stats-4">
            {tiles.map(({ key, ...t }) => (
              <StatCard key={t.label} {...t} scope="servers" active={key ? !!active[key] : undefined} onClick={key ? () => toggle(key) : undefined} />
            ))}
          </div>
          <FilterBubbles items={bubbles} />
          {all.length === 0 && <p class="note" role="status">No SentinelOne agents were found.</p>}
          {all.length > 0 && rows.length === 0 && <p class="note" role="status">No endpoints match the filters.</p>}
          <div class="agent-grid">
            {view.items.map((s) => <ServerCard key={s.agent_id} s={s} grace={data.patch_info?.grace_days} signal={signal} />)}
          </div>
          <Pager view={view} size={size} sizes={CARD_SIZES} unit="Cards" onSize={(n) => { setSize(n); setPage(0); }} onPage={setPage} />
        </>
      )}
    </>
  );
}
