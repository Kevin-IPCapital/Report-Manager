import { useState } from 'preact/hooks';
import { api } from '../api/adapter';
import { useAuth } from '../auth/AuthContext';
import { PageHeader } from '../components/PageHeader';
import { linkedCustomers } from '../clientTable';
import { DataTable, ErrorNote } from '../components/ui';
import { useApi } from '../components/useApi';

const SOURCES = [['autotask', 'Autotask'], ['ncentral', 'N-central'], ['sentinelone', 'SentinelOne'], ['cove', 'Cove']];
const SOURCE_NAME = Object.fromEntries(SOURCES);
const ID_HINT = { autotask: 'Company ID', ncentral: 'Org unit ID', sentinelone: 'Site ID', cove: 'Customer ID' };

const COLUMNS = [
  { key: 'name', label: 'Client' }, { key: 'company_text', label: 'Company' }, { key: 'sources_text', label: 'Linked Customers', wrap: true },
  { key: 'aliases_text', label: 'Also Known As', wrap: true }, { key: 'contact_email', label: 'Contact Email' },
  { key: 'is_active', label: 'Active', type: 'bool' },
];
const show = (c) => ({ ...c, company_text: c.company?.name ?? 'Unclaimed', sources_text: linkedCustomers(c.sources, SOURCE_NAME), aliases_text: c.aliases.join(', ') });

function ClientForm({ client, onCancel, onSaved }) {
  const [form, setForm] = useState({ name: '', contact_email: '', notes: '', is_active: true, sources: [], ...client, aliasText: (client.aliases ?? []).join('\n') });
  const [state, setState] = useState({ busy: false, error: '' });
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });
  const setSource = (i, patch) => setForm({ ...form, sources: form.sources.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const submit = async (e) => {
    e.preventDefault();
    setState({ busy: true, error: '' });
    try {
      await api.saveClient({
        id: client.id, name: form.name, contact_email: form.contact_email, notes: form.notes, is_active: form.is_active,
        sources: form.sources.filter((s) => String(s.external_id).trim()),
        aliases: form.aliasText.split('\n').map((a) => a.trim()).filter(Boolean),
      });
      onSaved();
    } catch (err) { setState({ busy: false, error: err.message }); }
  };
  return (
    <form class="panel" onSubmit={submit}>
      <div class="panel-head"><h2>{client.id ? 'Edit Client' : 'Create Client'}</h2></div>
      <div class="form-grid">
        <label class="field">Name<input required value={form.name} onInput={set('name')} /></label>
        <label class="field">Contact Email<input type="email" value={form.contact_email} onInput={set('contact_email')} /></label>
        <label class="field wide">Also Known As (one name per line)<textarea rows="3" value={form.aliasText} onInput={set('aliasText')} /></label>
        <label class="field wide">Notes<textarea rows="2" value={form.notes} onInput={set('notes')} /></label>
        <label class="choice"><input type="checkbox" checked={form.is_active} onChange={set('is_active')} />Active</label>
      </div>
      <fieldset class="group">
        <legend>Linked Customers</legend>
        <p class="note">Each link ties this client to one customer in a source system, by that system’s own ID. Names are never used to match.</p>
        {form.sources.map((s, i) => (
          <div class="source-row" key={i}>
            <select value={s.source} onChange={(e) => setSource(i, { source: e.target.value })}>
              {SOURCES.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
            <input placeholder={ID_HINT[s.source]} value={s.external_id} onInput={(e) => setSource(i, { external_id: e.target.value })} />
            <button type="button" class="btn small" onClick={() => setForm({ ...form, sources: form.sources.filter((_, j) => j !== i) })}>Remove</button>
          </div>
        ))}
        <button type="button" class="btn small" onClick={() => setForm({ ...form, sources: [...form.sources, { source: 'autotask', external_id: '' }] })}>Add Link</button>
      </fieldset>
      {state.error && <p class="note error" role="alert">{state.error}</p>}
      <div class="toolbar">
        <button class="primary" disabled={state.busy}>{state.busy ? 'Saving…' : 'Save Client'}</button>
        <button type="button" class="btn" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

// Clients nobody has claimed yet. Only administrators can see them. An administrator claims one for their own company (after which nobody
// outside that company sees it); the platform owner chooses the company.
function UnclaimedClients({ clients, user, onDone }) {
  const owner = Boolean(user?.is_owner), mine = user?.company;
  const { data: companies } = useApi(() => (owner ? api.getCompanies() : Promise.resolve([])), [owner]);
  const [pick, setPick] = useState({});
  const [state, setState] = useState({ busy: 0, error: '' });
  if (clients.length === 0) return null;
  const claim = async (client, company) => {
    setState({ busy: client.id, error: '' });
    try { await api.claimClient(client.id, company); setState({ busy: 0, error: '' }); onDone(); }
    catch (e) { setState({ busy: 0, error: e.message }); }
  };
  return (
    <section class="panel">
      <div class="panel-head"><h2>Unclaimed Clients</h2><span class="count">{clients.length}</span></div>
      <p class="note">
        {owner ? 'Only administrators can see these until a company claims them. Choose the company for each.'
          : mine ? `Only administrators can see these until a company claims them. Claiming one makes it ${mine.name}'s: nobody outside ${mine.name} will see it.`
            : 'You are not part of a company, so you cannot claim clients.'}
      </p>
      <div class="table-wrap"><table>
        <tbody>
          {clients.map((c) => (
            <tr key={c.id}>
              <td><strong>{c.name}</strong></td>
              <td class="row-actions">
                {owner ? (
                  <>
                    <select aria-label={`Company for ${c.name}`} value={pick[c.id] ?? ''} onChange={(e) => setPick({ ...pick, [c.id]: e.currentTarget.value })}>
                      <option value="">Choose a company…</option>
                      {(companies ?? []).map((co) => <option key={co.id} value={co.id}>{co.name}</option>)}
                    </select>
                    <button class="btn small" disabled={!pick[c.id] || state.busy === c.id} onClick={() => claim(c, Number(pick[c.id]))}>Assign</button>
                  </>
                ) : mine && <button class="btn small" disabled={state.busy === c.id} onClick={() => claim(c)}>{`Claim For ${mine.name}`}</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
      {state.error && <p class="note error" role="alert">{state.error}</p>}
    </section>
  );
}

export function Clients({ title }) {
  const { user } = useAuth();
  const [version, setVersion] = useState(0);
  const [editing, setEditing] = useState(null); // null = closed, {} = new, a client = editing
  const { data, error } = useApi(() => api.getClients(), [version]);
  const refresh = () => { setEditing(null); setVersion((v) => v + 1); };
  return (
    <>
      <PageHeader title={title} range={false} />
      <div class="toolbar"><button class="primary" onClick={() => setEditing({})}>New Client</button></div>
      <p class="note">Customers in the source systems that are not linked to a client yet, and clients missing from a source, are on the <a href="/clients/resolution">Client Resolution</a> page.</p>
      {editing && <ClientForm key={editing.id ?? editing.name ?? 'new'} client={editing} onCancel={() => setEditing(null)} onSaved={refresh} />}
      <ErrorNote error={error} />
      {data && <UnclaimedClients clients={data.filter((c) => !c.company)} user={user} onDone={refresh} />}
      {data && <DataTable title="Clients" columns={COLUMNS} rows={data.map(show)} onRowClick={(row) => setEditing(data.find((c) => c.id === row.id))} />}
    </>
  );
}
