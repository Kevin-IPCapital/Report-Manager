import { useState } from 'preact/hooks';
import { api } from '../api/adapter';
import { DataAsOf } from '../components/DataAsOf';
import { PageHeader } from '../components/PageHeader';
import { DeviceFilter } from '../components/DeviceFilter';
import { Donut } from '../components/Donut';
import { BarChart } from '../components/BarChart';
import { ChartControls } from '../components/ChartCard';
import { DetailModal, fact } from '../components/DetailModal';
import { StatCard, DataTable, ErrorNote, FilterBubbles } from '../components/ui';
import { useApi } from '../components/useApi';
import { matchesDevice } from '../filter';
import { isFilters, isText } from '../persisted';
import { usePersistentState } from '../state/persist';
import { worstBackups } from '../worst';
import { formatBytes, titleCase } from '../text';

const HEALTH_COLOUR = { OK: 'var(--good)', WARNING: 'var(--warn)', CRITICAL: 'var(--bad)' };
const PALETTE = ['#4f8cff', '#2b2d9e', '#7a1f7a', '#d94fa3', '#e3703a', '#d4a017', '#c9504f', '#3a8f5c'];
const categoryLabel = (c) => titleCase(String(c).replace(/_/g, ' '));

// Each chart counts something per row: `tally(row)` lists the labels a row counts towards (a device can have several red flags).
// Clicking a slice or bar filters the page to the devices that count towards it.
const DIMENSIONS = [
  { key: 'health', label: 'Health', chart: 'Backups By Health', unit: 'devices', tally: (r) => [r.health], colour: (v) => HEALTH_COLOUR[v] ?? '#8e96b3' },
  { key: 'customer', label: 'Customer', chart: 'Backups By Customer', unit: 'devices', tally: (r) => [r.customer] },
  { key: 'device_type', label: 'Device Type', chart: 'Backups By Device Type', unit: 'devices', tally: (r) => [r.device_type] },
  { key: 'flag_category', label: 'Red Flag Category', chart: 'Red Flags By Category', unit: 'flags', bar: true, tally: (r) => r.flags.map((f) => categoryLabel(f.category)) },
];

const tallyBy = (rows, dimension) => {
  const counts = new Map();
  for (const row of rows) for (const label of dimension.tally(row)) counts.set(label, (counts.get(label) ?? 0) + 1);
  return counts;
};
const when = (iso) => (iso ? new Date(iso).toLocaleDateString() : 'never');

// Lines of text for the detail card.
const sourceLine = (s) => `${s.ds_name || s.ds_code}: ${s.status || 'unknown'}, last success ${when(s.last_success)}; ${s.ok_days ?? 0} ok, ${s.failed_days ?? 0} failed, ${s.warning_days ?? 0} warning, ${s.missed_days ?? 0} missed days; protected ${formatBytes(s.protected_bytes)}`;
const m365Line = (s) => `${s.source_label}: ${s.enabled ? 'enabled' : 'not enabled'}, ${s.coverage || 'coverage unknown'}, last success ${when(s.last_success)}; ${s.user_count ?? 0} users, ${s.item_count ?? 0} items`;

// Current backup state from Cove. One request returns every device; filters, charts and tiles are computed here.
export function Backups({ title }) {
  const { data, error } = useApi(() => api.getSection('backups'), []);
  const [savedFilters, setFilters] = usePersistentState('backups:filters', {}, isFilters);   // remembered between visits
  const [device, setDevice] = usePersistentState('backups:device', '', isText);
  const filters = Object.fromEntries(Object.entries(savedFilters).filter(([k]) => DIMENSIONS.some((d) => d.key === k)));
  const [pick, setPick] = useState(null);   // { row, detail, error } while a device card is open

  // Same value again (or empty) removes the filter; a different value replaces it.
  const toggle = (key, value) => setFilters((f) => {
    const { [key]: current, ...rest } = f;
    return value && current !== value ? { ...rest, [key]: value } : rest;
  });
  const open = async (row) => {
    setPick({ row, detail: null });
    try { setPick({ row, detail: await api.getSection('backup-detail', { device: row.device_id }) }); }
    catch (e) { setPick({ row, detail: null, error: e.message }); }
  };

  const all = data?.rows ?? [];
  const rows = worstBackups(all.filter((r) => DIMENSIONS.every((d) => !filters[d.key] || d.tally(r).includes(filters[d.key])) && matchesDevice(device, r.device_name, r.hostname)));
  const count = (pred) => rows.filter(pred).length;
  const health = (h) => count((r) => r.health === h);
  const tiles = [
    { label: 'Devices', value: rows.length },
    { label: 'OK', value: health('OK'), status: 'good', filter: ['health', 'OK'] },
    { label: 'Warning', value: health('WARNING'), status: health('WARNING') ? 'warn' : 'good', filter: ['health', 'WARNING'] },
    { label: 'Critical', value: health('CRITICAL'), status: health('CRITICAL') ? 'bad' : 'good', filter: ['health', 'CRITICAL'] },
    { label: 'Red Flags', value: rows.reduce((sum, r) => sum + (r.red_flag_count ?? 0), 0) },
    { label: 'Used Storage', value: formatBytes(rows.reduce((sum, r) => sum + (r.used_storage_bytes ?? 0), 0)) },
    { label: 'Servers', value: count((r) => r.device_type === 'Server') },
    { label: 'Microsoft 365', value: count((r) => r.device_type === 'Microsoft 365') },
  ];
  const bubbles = [
    ...DIMENSIONS.filter((d) => filters[d.key]).map((d) => ({ label: d.label, value: filters[d.key], onRemove: () => toggle(d.key, null) })),
    ...(device.trim() ? [{ label: 'Device', value: device.trim(), onRemove: () => setDevice('') }] : []),
  ];

  const card = pick && (() => {
    const d = pick.detail?.device ?? pick.row;
    const flags = pick.detail?.flags ?? pick.row.flags;
    return (
      <DetailModal kicker={d.customer} title={d.device_name} banner={flags.length ? `⚑ ${flags.length} red flag(s)` : null}
        facts={[
          fact(d, 'Customer', 'customer'), fact(d, 'Device Type', 'device_type'), fact(d, 'Health', 'health', 'health'),
          fact(d, 'Last Success', 'last_success', 'datetime'), fact(d, 'Days Since Success', 'days_since_success'),
          fact(d, 'Used Storage', 'used_storage_bytes', 'bytes'), fact(d, 'Retention (Days)', 'retention_days'),
          fact(d, 'Cloud Storage', 'cloud_status'), fact(d, 'LSV', 'lsv_status'), fact(d, 'Immutable', 'immutable_enabled', 'flag'),
          ...(pick.detail ? [fact(d, 'Operating System', 'os'), fact(d, 'Cove Client Version', 'client_version'), fact(d, 'Storage Location', 'storage_location'),
            fact(d, 'Contact', 'contact_email'), fact(d, 'Collected', 'captured_at', 'datetime')] : []),
        ]}
        blocks={pick.error ? [['Details', `Could not load the details: ${pick.error}`]]
          : !pick.detail ? [['Details', 'Loading…']]
          : [['Red Flags', flags.map((f) => `${categoryLabel(f.category)}: ${f.flag}`).join('\n') || 'None'],
             ['Data Sources', pick.detail.datasources.map(sourceLine).join('\n') || 'None recorded'],
             ...(pick.detail.m365.length ? [['Microsoft 365 Sources', pick.detail.m365.map(m365Line).join('\n')]] : [])]}
        onClose={() => setPick(null)} />
    );
  })();

  return (
    <>
      <PageHeader title={title} range={false} />
      <DataAsOf source="backups" />
      <ErrorNote error={error} />
      {data && !data.available && (
        <p class="note" role="status">The Cove schema was not found in the reporting database. Apply <code>Collector/Cove-main/sql/schema.sql</code> first (see docs/05_OPERATIONS.md).</p>
      )}
      {data && data.available && !data.loaded && (
        <p class="note" role="status">No backup data has been collected yet. The Cove collector ships disabled: add your customers to <code>cove.customers</code>, run
          {' '}<code>python manage.py run_collection cove_backup</code> once, then enable the job (docs/05_OPERATIONS.md).</p>
      )}
      {data?.loaded && (
        <>
          <div class="toolbar"><DeviceFilter value={device} onChange={setDevice} names={all.flatMap((r) => [r.device_name, r.hostname])} /></div>
          <div class="stats stats-4">
            {tiles.map(({ filter, ...t }) => (
              <StatCard key={t.label} {...t} scope="backups" active={filter ? filters[filter[0]] === filter[1] : undefined} onClick={filter ? () => toggle(filter[0], filter[1]) : undefined} />
            ))}
          </div>
          <ChartControls>
            <div class="charts">
              {DIMENSIONS.map((d) => {
                const labels = [...tallyBy(all, d).entries()].sort((a, b) => b[1] - a[1]).map(([label]) => label);   // stable order and colours across filters
                const colorOf = d.colour ?? ((l) => PALETTE[labels.indexOf(l) % PALETTE.length]);
                const counts = Object.fromEntries(tallyBy(rows, d));
                const Chart = d.bar ? BarChart : Donut;
                return <Chart key={d.key} title={d.chart} unit={d.unit} labels={labels} counts={counts} selected={filters[d.key]} onSelect={(v) => toggle(d.key, v)} colorOf={colorOf} />;
              })}
            </div>
          </ChartControls>
          <DataTable title={data.title} persistKey="backups" columns={data.columns} rows={rows} onRowClick={open} above={<FilterBubbles items={bubbles} />} />
        </>
      )}
      {card}
    </>
  );
}
