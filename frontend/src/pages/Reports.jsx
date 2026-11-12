import { useEffect, useState } from 'preact/hooks';
import { api } from '../api/adapter';
import { useAuth } from '../auth/AuthContext';
import { PageHeader } from '../components/PageHeader';
import { ChoiceFilter } from '../components/DataFilter';
import { MultiFilter } from '../components/MultiFilter';
import { WeightFields } from '../components/WeightFields';
import { ErrorNote } from '../components/ui';
import { useApi } from '../components/useApi';
import { BLOCK_FILTERS, BLOCK_LABELS, BLOCK_TYPES, DEFAULT_CONFIG, DURATION_UNITS, FILTER_LABELS, MAX_BLOCKS, newBlock, validateTemplate } from '../report/config';
import { describeDuration, durationRange, presets, resolveDates } from '../report/dates';
import { reportActions, splitReports } from '../report/lists';
import { savedPresets } from '../ranges';
import { useSavedRanges } from '../state/SavedRangesContext';
import { iso } from '../dateMath';
import { decodeRange, encodeRange, isRange } from '../rangeMemory';
import { usePersistentState } from '../state/persist';
import { extractReport } from '../report/extract';
import { collect } from '../report/fetch';
import { buildPdf, saveFile } from '../report/pdf';

const clone = (value) => JSON.parse(JSON.stringify(value));
const uniqueValues = (rows, field) => [...new Set((rows ?? []).map((r) => String(r[field] ?? '').trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
const nextId = (blocks) => { let n = blocks.length + 1; while (blocks.some((b) => b.id === `b${n}`)) n += 1; return `b${n}`; };
const slug = (text) => String(text).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'report';
const when = (iso) => (iso ? new Date(iso).toLocaleDateString() : '');

// Where the choices for each filter come from: [which data, which field].
const CHOICES = {
  tickets: { customer: ['tickets', 'company_name'], priority: ['tickets', 'priority'], ticketStatus: ['tickets', 'status'], ticketType: ['tickets', 'ticket_type'], issueType: ['tickets', 'issue_type'], subIssueType: ['tickets', 'sub_issue_type'] },
  'server-health': { customer: ['health', 'customer_name'], agentStatus: ['health', 'network_status'] },
  backups: { customer: ['backups', 'customer'], backupHealth: ['backups', 'health'] },
};

// One filter box of one block.
function BlockFilter({ block, name, value, datasets, loading, onChange }) {
  const label = FILTER_LABELS[name];
  if (name === 'patchScope') return <ChoiceFilter label={label} value={value} loading={loading} onChange={onChange} choices={(datasets.patches?.clients ?? []).map((c) => ({ value: c.key, label: c.label }))} />;   // one client only
  if (name === 'patchType') return <MultiFilter label={label} value={value} loading={loading} onChange={onChange} options={(datasets.patches?.types ?? []).map((t) => ({ value: t.key, label: t.label }))} />;
  const [source, field] = CHOICES[block.type][name];
  return <MultiFilter label={label} value={value} loading={loading} onChange={onChange} options={uniqueValues(datasets[source], field)} />;
}

// Choose the dates and make the PDF. Dates are asked for every time, so a saved report never needs editing just to change the period.
function RunPanel({ report, onClose }) {
  const saved = report.config.period;
  const { ranges } = useSavedRanges();
  // The last range used is remembered (a range ending today as its length, so it stays "the last N days"). A report with a default
  // period of its own starts from that instead.
  const [remembered, remember] = usePersistentState('report:range', null, isRange);
  const [dates, setDates] = useState(() => (saved.from || saved.to || saved.last ? resolveDates(null, saved) : decodeRange(remembered, iso(new Date())) ?? resolveDates(null, null)));
  const [state, setState] = useState({ busy: false, error: '', notice: '' });
  const setDate = (key, value) => setDates({ ...dates, [key]: value });

  async function generate() {
    setState({ busy: true, error: '', notice: '' });
    try {
      const config = validateTemplate(report.config);
      const period = resolveDates(dates, null);
      const data = await collect(config, period, api.getSection);
      const { sections, annex, warnings } = extractReport(config, data, period);
      if (!sections.length) throw new Error(warnings.join(' ') || 'No live data matched the selected sections.');
      const stamp = new Date().toISOString().slice(0, 10);
      saveFile(buildPdf({ title: config.title, subtitle: 'Live source data', period, sections, annex }), `${slug(report.name || config.title)}-${stamp}.pdf`);
      remember(encodeRange(period, iso(new Date())));
      setState({ busy: false, error: '', notice: `PDF generated: ${sections.length} section(s)${annex.length ? ` and ${annex.length} annex table(s)` : ''}. ${warnings.join(' ')}` });
    } catch (e) { setState({ busy: false, error: e.message || 'Report export failed.', notice: '' }); }
  }

  return (
    <section class="panel" aria-label="Generate report">
      <div class="panel-head"><h2>Generate “{report.name || report.config.title}”</h2><button class="btn small" onClick={onClose}>Close</button></div>
      <p class="note">The period applies to the ticket sections. Everything else shows the current state.</p>
      <div class="toolbar">
        {[...presets(), ...savedPresets(ranges)].map((p) => <button key={p.key} class="btn small" title={p.title} onClick={() => setDates({ from: p.from, to: p.to })}>{p.label}</button>)}
      </div>
      <div class="toolbar">
        <label class="field">From<input type="date" value={dates.from} onInput={(e) => setDate('from', e.currentTarget.value)} /></label>
        <label class="field">To<input type="date" value={dates.to} onInput={(e) => setDate('to', e.currentTarget.value)} /></label>
        <button class="primary" disabled={state.busy} onClick={generate}>{state.busy ? 'Generating…' : 'Generate PDF'}</button>
      </div>
      {state.error && <p class="note error" role="alert">{state.error}</p>}
      {state.notice && <p class="note" role="status">{state.notice}</p>}
    </section>
  );
}

// Create or change a report: its name and options, then its sections (blocks), each with its own filters.
function ReportEditor({ initial, canShare, onSave, onCancel, onGenerate }) {
  const { ranges } = useSavedRanges();
  const [name, setName] = useState(initial.name);
  const [shared, setShared] = useState(initial.shared);
  const [cfg, setCfg] = useState(() => clone(initial.config));
  const [addType, setAddType] = useState(BLOCK_TYPES[0]);
  const [periodMode, setPeriodMode] = useState(() => (initial.config.period.last ? 'last' : initial.config.period.from || initial.config.period.to ? 'dates' : 'ask'));
  const [weightsOn, setWeightsOn] = useState(Object.keys(initial.config.weights ?? {}).length > 0);
  const [weightDraft, setWeightDraft] = useState(() => Object.fromEntries(Object.entries(initial.config.weights ?? {}).map(([k, v]) => [k, String(v)])));
  const [datasets, setDatasets] = useState({});
  const [sharedWeights, setSharedWeights] = useState({});
  const [loading, setLoading] = useState(false);
  const [state, setState] = useState({ busy: false, error: '' });

  const types = [...new Set(cfg.blocks.map((b) => b.type))];
  const usesPatches = types.includes('patches') || types.includes('patch-details');
  const need = (...kinds) => kinds.some((k) => types.includes(k));

  // The choices in the filter boxes come from the data itself.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      const next = {};
      try {
        if (need('tickets')) { let dates; try { dates = resolveDates(null, cfg.period); } catch { dates = resolveDates(null, null); } next.tickets = (await api.getSection('tickets', dates)).tickets?.rows ?? []; }
        if (need('server-health')) next.health = (await api.getSection('server-health')).servers ?? [];
        if (need('backups')) next.backups = (await api.getSection('backups')).rows ?? [];
        if (usesPatches || weightsOn) next.patches = await api.getSection('patches');
        if (weightsOn) setSharedWeights((await api.getWeights()).shared ?? {});
      } catch (e) { if (!cancelled) setState((s) => ({ ...s, error: `Could not load filter choices: ${e.message}` })); }
      if (!cancelled) { setDatasets((d) => ({ ...d, ...next })); setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [types.join('|'), cfg.period.from, cfg.period.to, weightsOn]);

  const setBlock = (id, change) => setCfg((c) => ({ ...c, blocks: c.blocks.map((b) => (b.id === id ? { ...b, ...change } : b)) }));
  const setFilter = (id, key, value) => setCfg((c) => ({ ...c, blocks: c.blocks.map((b) => (b.id === id ? { ...b, filters: { ...b.filters, [key]: value } } : b)) }));
  const changeType = (id, type) => setCfg((c) => ({ ...c, blocks: c.blocks.map((b) => (b.id === id ? { ...newBlock(type, id), title: b.title } : b)) }));
  const move = (index, step) => setCfg((c) => {
    const blocks = [...c.blocks], to = index + step;
    if (to < 0 || to >= blocks.length) return c;
    [blocks[index], blocks[to]] = [blocks[to], blocks[index]];
    return { ...c, blocks };
  });
  // The default period: nothing (asked for each time), fixed dates, or "the last N days / weeks / months", worked out when the report runs.
  const choosePeriod = (mode) => {
    setPeriodMode(mode);
    setCfg((c) => ({ ...c, period: mode === 'last' ? { from: '', to: '', last: c.period.last ?? { amount: 30, unit: 'days' } } : { from: '', to: '' } }));
  };
  const setLast = (change) => setCfg((c) => ({ ...c, period: { from: '', to: '', last: { ...c.period.last, ...change } } }));
  const lastNow = (() => { try { const r = durationRange(cfg.period.last, new Date()); return `${r.from} to ${r.to}`; } catch { return ''; } })();
  const remove = (id) => setCfg((c) => ({ ...c, blocks: c.blocks.filter((b) => b.id !== id) }));
  const add = () => setCfg((c) => (c.blocks.length >= MAX_BLOCKS ? c : { ...c, blocks: [...c.blocks, newBlock(addType, nextId(c.blocks))] }));

  // The definition as it will be saved: normalised by the same rules the server applies.
  const build = () => validateTemplate({
    ...cfg, maxRows: Number(cfg.maxRows),
    weights: weightsOn && usesPatches ? Object.fromEntries(Object.entries(weightDraft).filter(([, v]) => String(v).trim() !== '').map(([k, v]) => [k, Number(v)])) : {},
  });
  const save = async () => {
    setState({ busy: true, error: '' });
    try { await onSave({ id: initial.id, name: name.trim(), shared, config: build() }); }
    catch (e) { setState({ busy: false, error: e.message }); }
  };
  const generate = () => {
    try { onGenerate({ id: initial.id, name: name.trim() || cfg.title, config: build() }); setState((s) => ({ ...s, error: '' })); }
    catch (e) { setState((s) => ({ ...s, error: e.message })); }
  };

  return (
    <section class="panel" aria-label="Report editor">
      <div class="panel-head"><h2>{initial.id ? 'Edit Report' : 'New Report'}</h2></div>
      <div class="toolbar">
        <label class="field">Report name (in this list)<input value={name} maxLength={120} onInput={(e) => setName(e.currentTarget.value)} /></label>
        <label class="field">Title on the PDF<input value={cfg.title} maxLength={120} onInput={(e) => setCfg({ ...cfg, title: e.currentTarget.value })} /></label>
        <label class="field">Detail rows per table<input type="number" min="10" max="500" value={cfg.maxRows} onInput={(e) => setCfg({ ...cfg, maxRows: e.currentTarget.value })} /></label>
      </div>
      <div class="toolbar">
        <label class="field">Default period
          <select value={periodMode} onChange={(e) => choosePeriod(e.currentTarget.value)}>
            <option value="ask">Choose when generating</option>
            <option value="last">The last…</option>
            <option value="dates">Fixed dates</option>
          </select>
        </label>
        {periodMode === 'last' && (
          <>
            {ranges.length > 0 && (
              <label class="field">Use A Saved Range
                <select value="" onChange={(e) => { const r = ranges.find((x) => x.name === e.currentTarget.value); if (r) setLast({ amount: r.amount, unit: r.unit }); }}>
                  <option value="">Choose…</option>
                  {ranges.map((r) => <option key={r.name} value={r.name}>{r.name} ({describeDuration(r).toLowerCase()})</option>)}
                </select>
              </label>
            )}
            <label class="field">Amount<input type="number" min="1" max={DURATION_UNITS[cfg.period.last?.unit ?? 'days']} value={cfg.period.last?.amount ?? ''} onInput={(e) => setLast({ amount: Number(e.currentTarget.value) })} /></label>
            <label class="field">Unit
              <select value={cfg.period.last?.unit ?? 'days'} onChange={(e) => setLast({ unit: e.currentTarget.value })}>{Object.keys(DURATION_UNITS).map((u) => <option key={u} value={u}>{u}</option>)}</select>
            </label>
            <span class="note">{cfg.period.last ? describeDuration(cfg.period.last) : ''}{lastNow ? `: today that is ${lastNow}. ` : '. '}It is worked out from the day the report is generated.</span>
          </>
        )}
        {periodMode === 'dates' && (
          <>
            <label class="field">From<input type="date" value={cfg.period.from} onInput={(e) => setCfg({ ...cfg, period: { from: e.currentTarget.value, to: cfg.period.to } })} /></label>
            <label class="field">To<input type="date" value={cfg.period.to} onInput={(e) => setCfg({ ...cfg, period: { from: cfg.period.from, to: e.currentTarget.value } })} /></label>
          </>
        )}
        {periodMode === 'ask' && <span class="note">The last 30 days, or the range you used last time, is suggested. You can always choose other dates when you generate the report.</span>}
      </div>
      <label class="check"><input type="checkbox" checked={cfg.annex} onChange={(e) => setCfg({ ...cfg, annex: e.currentTarget.checked })} /> Include the detail tables as an annex at the back (without it the report is charts and totals only)</label>
      {canShare && <label class="check"><input type="checkbox" checked={shared} onChange={(e) => setShared(e.currentTarget.checked)} /> Make this a template everyone can use (they can change only the dates)</label>}

      <h3>Sections</h3>
      {cfg.blocks.length === 0 && <p class="note">Add at least one section.</p>}
      {cfg.blocks.map((block, index) => (
        <div class="block-card" key={block.id}>
          <div class="block-head">
            <strong>{index + 1}.</strong>
            <select aria-label="Section type" value={block.type} onChange={(e) => changeType(block.id, e.currentTarget.value)}>
              {BLOCK_TYPES.map((t) => <option key={t} value={t}>{BLOCK_LABELS[t]}</option>)}
            </select>
            <label class="field inline">Heading (optional)<input value={block.title} maxLength={120} placeholder={BLOCK_LABELS[block.type]} onInput={(e) => setBlock(block.id, { title: e.currentTarget.value })} /></label>
            <span class="block-actions">
              <button class="btn small" disabled={index === 0} onClick={() => move(index, -1)} aria-label="Move up">↑</button>
              <button class="btn small" disabled={index === cfg.blocks.length - 1} onClick={() => move(index, 1)} aria-label="Move down">↓</button>
              <button class="btn small" onClick={() => remove(block.id)}>Remove</button>
            </span>
          </div>
          <div class="block-filters">
            {BLOCK_FILTERS[block.type].map((key) => (
              <BlockFilter key={key} block={block} name={key} value={block.filters[key]} datasets={datasets} loading={loading} onChange={(v) => setFilter(block.id, key, v)} />
            ))}
          </div>
        </div>
      ))}
      <div class="toolbar">
        <select aria-label="Section to add" value={addType} onChange={(e) => setAddType(e.currentTarget.value)}>{BLOCK_TYPES.map((t) => <option key={t} value={t}>{BLOCK_LABELS[t]}</option>)}</select>
        <button class="btn" disabled={cfg.blocks.length >= MAX_BLOCKS} onClick={add}>Add Section</button>
        <span class="note">{cfg.blocks.length} of {MAX_BLOCKS}. Add the same kind of section twice with different filters, for example tickets for two customers.</span>
      </div>

      {usesPatches && (
        <>
          <label class="check"><input type="checkbox" checked={weightsOn} onChange={(e) => setWeightsOn(e.currentTarget.checked)} /> This report uses its own patch score weights</label>
          {weightsOn && (
            <>
              <p class="note">These replace your personal weights for this report only, on top of the shared defaults, so everyone who runs it gets the same scores. A blank box uses the shared default (grey number); 0 leaves that item out.</p>
              {datasets.patches ? <WeightFields types={datasets.patches.types ?? []} draft={weightDraft} setDraft={setWeightDraft} inherited={sharedWeights} /> : <p class="note">Loading patch categories…</p>}
            </>
          )}
        </>
      )}

      {state.error && <p class="note error" role="alert">{state.error}</p>}
      <div class="toolbar">
        <button class="primary" disabled={state.busy || !name.trim()} onClick={save}>{state.busy ? 'Saving…' : 'Save Report'}</button>
        <button class="btn" onClick={generate}>Generate PDF…</button>
        <button class="btn" onClick={onCancel}>Cancel</button>
        {!name.trim() && <span class="note">Give the report a name to save it.</span>}
      </div>
    </section>
  );
}

// Saved reports: run, edit, copy, export, delete. A definition is saved on the server; the PDF is made in the browser.
// One list of reports: personal reports or templates. What each person may do is decided in report/lists.js; the server enforces it too.
function ReportTable({ rows, user, onGenerate, onEdit, onCopy, onExport, onRemove }) {
  return (
    <div class="table-wrap"><table>
      <thead><tr><th>Name</th><th>Sections</th><th>Owner</th><th>Updated</th><th /></tr></thead>
      <tbody>
        {rows.map((r) => {
          const can = reportActions(r, user);
          return (
            <tr key={r.id}>
              <td><strong>{r.name}</strong><div class="note">{r.config.title}</div></td>
              <td>{r.config.blocks.map((b) => b.title || BLOCK_LABELS[b.type]).join(', ')}</td>
              <td>{r.mine ? 'You' : r.owner}</td>
              <td>{when(r.updated_at)}</td>
              <td class="row-actions">
                {can.generate && <button class="btn small" onClick={() => onGenerate(r)}>Generate</button>}
                {can.edit && <button class="btn small" onClick={() => onEdit(r)}>Edit</button>}
                {can.copy && <button class="btn small" onClick={() => onCopy(r)}>{can.copyLabel}</button>}
                {can.export && <button class="btn small" onClick={() => onExport(r)}>Export</button>}
                {can.remove && <button class="btn small" onClick={() => onRemove(r)}>Delete</button>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table></div>
  );
}

export function Reports({ title }) {
  const { user } = useAuth();
  const [version, setVersion] = useState(0);
  const { data: reports, error } = useApi(() => api.getReports(), [version]);
  const [editing, setEditing] = useState(null);   // { id?, name, shared, config }
  const [running, setRunning] = useState(null);   // { name, config }
  const [message, setMessage] = useState({ text: '', error: '' });
  const refresh = () => setVersion((v) => v + 1);
  const say = (text, err = '') => setMessage({ text, error: err });
  const { mine, templates } = splitReports(reports ?? []);

  const save = async (report) => {
    await api.saveReport(report);
    setEditing(null); refresh(); say(`Saved “${report.name}”.`);
  };
  const remove = async (r) => {
    if (!window.confirm(`Delete the report “${r.name}”? This cannot be undone.`)) return;
    try { await api.deleteReport(r.id); refresh(); say(`Deleted “${r.name}”.`); } catch (e) { say('', e.message); }
  };
  const copy = async (r) => {
    const own = new Set((reports ?? []).filter((x) => x.mine).map((x) => x.name.toLowerCase()));
    let name = `${r.name} (copy)`, n = 2;
    while (own.has(name.toLowerCase())) { name = `${r.name} (copy ${n})`; n += 1; }
    try { await api.saveReport({ name, config: r.config }); refresh(); say(`Copied to “${name}”.`); } catch (e) { say('', e.message); }
  };
  const exportJson = (r) => { saveFile(new Blob([JSON.stringify(r.config, null, 2)], { type: 'application/json' }), `${slug(r.name)}.json`); say(`Exported “${r.name}”.`); };
  const importJson = async (e) => {
    const file = e.currentTarget.files?.[0];
    e.currentTarget.value = '';
    if (!file) return;
    try {
      if (file.size > 100000) throw new Error('The file is too large.');
      const config = validateTemplate(JSON.parse(await file.text()));
      setEditing({ name: file.name.replace(/\.json$/i, ''), shared: false, config });
      setRunning(null); say('Imported: check it, then save it.');
    } catch (err) { say('', `Cannot import: ${err.message}`); }
  };

  const handlers = {
    onGenerate: (r) => { setEditing(null); setRunning({ id: r.id, name: r.name, config: r.config }); },
    onEdit: (r) => { setRunning(null); setEditing({ id: r.id, name: r.name, shared: r.shared, config: r.config }); },
    onCopy: copy, onExport: exportJson, onRemove: remove,
  };

  return (
    <>
      <PageHeader title={title} range={false} />
      <ErrorNote error={error} />
      {message.text && <p class="note" role="status">{message.text}</p>}
      {message.error && <p class="note error" role="alert">{message.error}</p>}
      {running && <RunPanel key={running.name + (running.id ?? '')} report={running} onClose={() => setRunning(null)} />}
      {editing ? (
        <ReportEditor initial={editing} canShare={!!user?.is_staff} onSave={save} onCancel={() => setEditing(null)} onGenerate={(r) => setRunning(r)} />
      ) : (
        <>
          <section class="panel">
            <div class="panel-head">
              <h2>My Reports</h2>
              <div class="panel-actions">
                <button class="primary" onClick={() => { setRunning(null); setEditing({ name: '', shared: false, config: clone(DEFAULT_CONFIG) }); }}>New Report</button>
                <label class="btn">Import JSON<input type="file" accept="application/json,.json" hidden onChange={importJson} /></label>
              </div>
            </div>
            <p class="note">Your own reports, private to you. Copy a template to start from one.</p>
            {reports && mine.length === 0 && <p class="note">No reports of your own yet. Create one, copy a template, or import a file.</p>}
            {mine.length > 0 && <ReportTable rows={mine} user={user} {...handlers} />}
          </section>
          <section class="panel">
            <div class="panel-head">
              <h2>Templates</h2>
              {user?.is_staff && <div class="panel-actions"><button class="btn" onClick={() => { setRunning(null); setEditing({ name: '', shared: true, config: clone(DEFAULT_CONFIG) }); }}>New Template</button></div>}
            </div>
            <p class="note">Made by administrators for everyone. Generate one with your own dates; only its creator or an administrator can change it.</p>
            {reports && templates.length === 0 && <p class="note">No templates yet.</p>}
            {templates.length > 0 && <ReportTable rows={templates} user={user} {...handlers} />}
          </section>
        </>
      )}
    </>
  );
}
