import { useEffect, useState } from 'preact/hooks';
import { api } from '../api/adapter';
import { useAuth } from '../auth/AuthContext';
import { PageHeader } from '../components/PageHeader';
import { TrendChart } from '../components/TrendChart';
import { DetailModal, fact } from '../components/DetailModal';
import { WeightFields } from '../components/WeightFields';
import { DataTable, ErrorNote, FilterBubbles } from '../components/ui';
import { useApi } from '../components/useApi';
import { hint } from '../glossary';
import { ChartCard } from '../components/ChartCard';
import { DataAsOf } from '../components/DataAsOf';
import { ScoreWorking } from '../components/ScoreWorking';
import { DeviceFilter } from '../components/DeviceFilter';
import { matchesDevice } from '../filter';
import { isText } from '../persisted';
import { CHOOSE_ONE, MAX_CHOICES, isChoices, keepKnown, labelOf, pickType, scopeParams, singleChoice, toggleChoice } from '../overview';
import { iso } from '../dateMath';
import { isTrendRangeKey, limitTrend, trendRangeOptions } from '../trend';
import { useSavedRanges } from '../state/SavedRangesContext';
import { usePersistentState } from '../state/persist';
import { worstCategories } from '../worst';

const pct = (v) => (v == null ? '—' : `${Math.round(v)}%`);

// Weights are saved on the server. Everyone has their own set, layered over the shared default set; a blank box
// means "use the default". Staff can switch to editing the shared set. Scores are recalculated by the server.
function WeightsPanel({ types, calculation, clientName, onSaved }) {
  const { user } = useAuth();
  const [version, setVersion] = useState(0);
  const { data, error } = useApi(() => api.getWeights(), [version]);
  const [scope, setScope] = useState('user');
  const [draft, setDraft] = useState({});
  const [state, setState] = useState({ busy: false, error: '' });

  useEffect(() => {
    if (data) setDraft(Object.fromEntries(Object.entries(data[scope]).map(([k, v]) => [k, String(v)])));
  }, [data, scope]);

  const inherited = scope === 'user' ? data?.shared ?? {} : {};
  const save = async (weights) => {
    setState({ busy: true, error: '' });
    try { await api.saveWeights(scope, weights); setVersion((v) => v + 1); onSaved(); setState({ busy: false, error: '' }); }
    catch (e) { setState({ busy: false, error: e.message }); }
  };
  const submit = () => save(Object.fromEntries(Object.entries(draft).filter(([, v]) => v.trim() !== '').map(([k, v]) => [k, Number(v)])));

  return (
    <section class="panel">
      <div class="panel-head">
        <h2 {...hint('Score Weights')}>Score Weights</h2>
        <div class="panel-actions">
          {user?.is_staff && (
            <label class="field">Editing
              <select value={scope} onChange={(e) => setScope(e.target.value)}>
                <option value="user">My weights</option><option value="shared">Shared default (everyone)</option>
              </select>
            </label>
          )}
        </div>
      </div>
      <p class="note">A category’s weight sets how much it counts towards its type’s score, and a type’s weight how much it counts towards the overall
        score. The grey number is what applies when the box is blank (the shared default, else 1). 0 leaves that item out.
        The shared default is also used for the saved score history behind the trend.</p>
      <ScoreWorking calculation={calculation} clientName={clientName} />
      <ErrorNote error={error} />
      {state.error && <p class="note error" role="alert">{state.error}</p>}
      {data && <WeightFields types={types} draft={draft} setDraft={setDraft} inherited={inherited} />}
      <div class="toolbar">
        <button class="primary" disabled={state.busy} onClick={submit}>{state.busy ? 'Saving…' : 'Save Weights'}</button>
        <button class="btn" disabled={state.busy} onClick={() => save({})}>{scope === 'user' ? 'Use Shared Defaults' : 'Clear Shared Weights'}</button>
      </div>
    </section>
  );
}

// The score history comes from snapshots the server saves after every patch collection; it is empty until there are some.
function TrendPanel({ filter, scope }) {
  const { data, error } = useApi(() => api.getSection('patch-trend', { ...filter, scope }), [filter.client, filter.org, scope]);
  const { ranges: myRanges } = useSavedRanges();   // the date ranges this person named and saved
  const [range, setRange] = usePersistentState('patches:trend-range', 'all', isTrendRangeKey);   // remembered: a built-in range, or one you saved
  if (error) return <section class="panel"><ErrorNote error={error} /></section>;
  if (!data) return null;
  const shown = limitTrend(data, range, iso(new Date()), myRanges);
  const saved = data.y.length > 0;   // are there any saved scores at all
  return (
    <ChartCard title={data.heading} total={shown.y.length} unit="saved scores">
      {saved && (
        <label class="field">Range
          <select value={range} onChange={(e) => setRange(e.currentTarget.value)}>
            {trendRangeOptions(myRanges).map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
        </label>
      )}
      {saved ? (shown.y.length ? <TrendChart {...data} x={shown.x} y={shown.y} showHeading={false} /> : <p class="note">No scores were saved in this range. Choose a longer one.</p>)
        : <p class="note">{data.heading}. Scores are saved after each patch collection.</p>}
    </ChartCard>
  );
}

const patchFacts = (p) => [
  fact(p, 'Device', 'device'), fact(p, 'Device Id', 'device_id'), fact(p, 'KB', 'kb'), fact(p, 'Category', 'category'), fact(p, 'Category Id', 'category_id'),
  fact(p, 'Installed', 'installed', 'bool'), fact(p, 'Installed At', 'installed_at', 'datetime'), fact(p, 'Result', 'result'),
  fact(p, 'Security', 'is_security', 'flag'), fact(p, 'Third Party', 'is_third_party', 'flag'), fact(p, 'Run', 'run_id'),
];

// The patch list is fetched only when its level is opened, because it can be large.
function PatchEntries({ type, category, filter, above }) {
  const [entry, setEntry] = useState(null);
  const [device, setDevice] = usePersistentState('patches:device', '', isText);   // remembered
  const { data, error } = useApi(() => api.getSection('patch-entries', { type, category: category.id, ...filter }), [type, category.id, filter.client, filter.org]);
  const rows = data ? data.entries.rows.filter((r) => matchesDevice(device, r.device)) : [];
  const bubbles = device.trim() ? <FilterBubbles items={[{ label: 'Device', value: device.trim(), onRemove: () => setDevice('') }]} /> : null;
  return (
    <>
      <ErrorNote error={error} />
      {data && <div class="toolbar"><DeviceFilter value={device} onChange={setDevice} names={data.entries.rows.map((r) => r.device)} /></div>}
      {data && <DataTable {...data.entries} rows={rows} persistKey="patch-entries" above={<>{above}{bubbles}</>} onRowClick={setEntry} />}
      {entry && <DetailModal kicker={entry.kb} title={entry.title} facts={patchFacts(entry)} onClose={() => setEntry(null)} />}
    </>
  );
}

const pct2 = (v) => (v == null ? '—' : `${v.toFixed(2)}%`);

// Every client you may see, with its device count, as buttons. Clicking one adds it to the filter (clicking it again removes it). This card is
// never narrowed by the filter, so every client stays on screen with the chosen ones highlighted.
function ClientsCard({ clients, choices, onToggle }) {
  return (
    <section class="panel clients-card col-12 col-lg-7 col-xl-8">
      <h2>Clients</h2>
      {clients.length === 0 ? <p class="note">No clients have patch data yet.</p> : (
        <div class="client-chips" role="group" aria-label="Filter by client">
          {clients.map((c) => (
            <button key={c.key} type="button" class={`chip${choices.includes(c.key) ? ' on' : ''}`} aria-pressed={choices.includes(c.key)}
              title={c.linked ? undefined : 'Not linked to a client yet'} onClick={() => onToggle(c.key)}>
              {c.label} <span class="chip-count">{c.devices} device(s)</span>{c.linked ? '' : ' · not linked'}
            </button>
          ))}
        </div>
      )}
      {choices.length >= MAX_CHOICES && <p class="note">At most {MAX_CHOICES} clients can be chosen.</p>}
    </section>
  );
}

// Scores By Client: a group for each client in name order, every client you may see, or only the chosen ones. Clicking a client's name
// shows only that client; clicking a measure (a type row) shows that client's categories below.
function ClientScores({ clients, choices, type, onPickClient, onPickType }) {
  const shown = choices.length ? clients.filter((c) => choices.includes(c.key)) : clients;
  const only = choices.length === 1 ? choices[0] : null;
  const measures = (rows) => rows.filter((r) => !r.group);
  return (
    <section class="panel">
      <div class="panel-head"><h2>Scores By Client</h2><span class="count">{shown.length} of {clients.length} client(s)</span></div>
      <p class="note">Click a client's name to show only that client, or a measure to see that client's categories below. Scores use your weights.</p>
      <div class="table-wrap">
        <table class="scores client-scores">
          <thead><tr><th><span class="sr-only">Client and measure</span></th><th class="num"><span {...hint('Current Score')}>Current Score</span></th><th class="num"><span {...hint('Previous Score')}>Previous Score</span></th></tr></thead>
          {shown.map((c) => (
            <tbody key={c.key}>
              <tr class={`client-row${choices.includes(c.key) ? ' selected' : ''}`}>
                <th scope="rowgroup">
                  <button type="button" class="rowlink" title="Show only this client" onClick={() => onPickClient(c.key)}>{c.label}</button>
                  <span class="note"> {c.devices} device(s){c.linked ? '' : ' · not linked to a client'}</span>
                </th>
                <td class="num cur"><strong>{pct2(c.overall.current)}</strong></td>
                <td class="num">{pct2(c.overall.previous)}</td>
              </tr>
              {measures(c.rows).map((r) => (
                <tr key={r.key} class={`clickable${only === c.key && type === r.key ? ' selected' : ''}`} onClick={() => onPickType(r.key, c.key)}>
                  <td class="indent"><button type="button" {...hint(r.label, 'rowlink')}>{r.label}</button></td>
                  <td class="num cur">{pct2(r.current)}</td>
                  <td class="num">{pct2(r.previous)}</td>
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </div>
    </section>
  );
}

// The Overview. The Overall Score card (for whatever is chosen) comes first, with the Clients card beside it, then the trend, the filter
// bubbles, and Scores By Client with the drill-down (measure, then category, then patches). The chosen clients are remembered.
export function Patches({ title }) {
  const [choices, setChoices] = usePersistentState('patches:clients', [], isChoices);   // "client:<id>" and "org:<N-central org unit id>", remembered
  const [version, setVersion] = useState(0);   // bumped after weights change, to fetch the new scores
  const overview = useApi(() => api.getSection('patch-overview'), [version]);   // every client: never narrowed by the filter
  const clients = overview.data?.clients ?? [];
  const filter = scopeParams(choices);
  const single = singleChoice(choices);   // the Overall Score and the trend are for one client; none or several get a prompt instead
  const { data, error } = useApi(() => api.getSection('patches', single ? filter : {}), [single ?? '', version]);   // several clients are never combined
  const [type, setType] = useState(null);
  const [category, setCategory] = useState(null);
  const [showTrend, setShowTrend] = useState(false);
  const [showWeights, setShowWeights] = useState(false);
  // A remembered choice that no longer exists (renamed, unlinked, removed) is dropped, so the page does not look empty for no reason.
  useEffect(() => { if (overview.data) { const kept = keepKnown(choices, clients); if (kept.length !== choices.length) setChoices(kept); } }, [overview.data]);

  const selected = data?.types.find((t) => t.key === type);
  const reset = () => { setType(null); setCategory(null); };
  const setScope = (next) => { setChoices(next); reset(); };
  const choose = (key, clientKey) => {
    const next = pickType({ choices, type }, key, clientKey);
    if (next.choices !== choices) setChoices(next.choices);
    setType(next.type);
    setCategory(null);
  };

  const clientBubbles = choices.map((key) => ({ label: 'Client', value: labelOf(key, clients), onRemove: () => setScope(choices.filter((k) => k !== key)) }));
  const typeBubble = selected && { label: 'Type', value: selected.label, onRemove: reset };
  const categoryBubble = category && { label: 'Category', value: category.name, onRemove: () => setCategory(null) };
  const trail = selected ? [{ label: selected.label, onClick: () => setCategory(null) }, ...(category ? [{ label: category.name }] : [])] : [];

  return (
    <>
      <PageHeader title={title} range={false} trail={trail} onTitle={reset} />
      <ErrorNote error={error ?? overview.error} />
      {data && single && !data.mapped && (
        <p class="note" role="status">This client has no N-central customer linked, so there are no patch results for it. Link one on the Clients page.</p>
      )}
      {data && (
        <>
          <div class="row row-tight">
            <section class="panel overall col-12 col-lg-5 col-xl-4">
              <h2 {...hint('Overall Score')}>Overall Score{single ? `: ${labelOf(single, clients)}` : ''}</h2>
              {single ? (
                <>
                  <strong class="overall-value">{pct(data.overall.current)}</strong>
                  <span class="note">Previous: {pct(data.overall.previous)}</span>
                </>
              ) : <p class="note choose-one" role="status">{CHOOSE_ONE}</p>}
              <DataAsOf source="patches" />
              <div class="panel-actions">
                <button class="btn" aria-expanded={showTrend} onClick={() => setShowTrend(!showTrend)}>{showTrend ? 'Hide Trend' : 'Show Trend'}</button>
                <button class="btn" aria-expanded={showWeights} onClick={() => setShowWeights(!showWeights)}>{showWeights ? 'Hide Weights' : 'Score Weights'}</button>
              </div>
            </section>
            <ClientsCard clients={clients} choices={choices} onToggle={(key) => setScope(toggleChoice(choices, key))} />
          </div>
          {showTrend && (single ? <TrendPanel filter={filter} scope={type ?? 'overall'} /> : <ChartCard title="Score Trend"><p class="note" role="status">{CHOOSE_ONE}</p></ChartCard>)}
          <FilterBubbles items={clientBubbles} />
          {showWeights && <WeightsPanel types={data.types} calculation={single ? data.calculation : null} clientName={single ? labelOf(single, clients) : ''} onSaved={() => setVersion((v) => v + 1)} />}
          <ClientScores clients={clients} choices={choices} type={type} onPickClient={(key) => setScope([key])} onPickType={choose} />
          {selected && (
            <DataTable title={`${selected.label} By Category`} columns={data.columns} rows={worstCategories(selected.categories)} persistKey="patch-categories"
              above={<FilterBubbles items={[typeBubble]} />} onRowClick={(r) => setCategory({ id: r.category_id, name: r.category })} />
          )}
          {selected && category && <PatchEntries type={type} category={category} filter={filter} above={<FilterBubbles items={[typeBubble, categoryBubble]} />} />}
        </>
      )}
    </>
  );
}
