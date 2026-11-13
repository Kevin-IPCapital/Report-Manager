import { useEffect, useState } from 'preact/hooks';
import { api } from '../api/adapter';
import { ACCESS_LEVELS, ACCESS_TABS, accessLabel, accessOf, accessRequest, emptyPerson, groupClients, groupRequest, personProblem, personRequest, sameIds, setupNotice } from '../access';
import { useAuth } from '../auth/AuthContext';
import { PageHeader } from '../components/PageHeader';
import { MultiFilter } from '../components/MultiFilter';
import { Tabs, tabPanelProps } from '../components/Tabs';
import { ErrorNote } from '../components/ui';
import { useApi } from '../components/useApi';
import { isOneOf } from '../persisted';
import { usePersistentState } from '../state/persist';

const emptyGroup = () => ({ name: '', all_clients: false, clients: [], company: '' });
const options = (rows) => rows.map((r) => ({ value: String(r.id), label: r.name }));
const asList = (v) => (Array.isArray(v) ? v : v ? [v] : []);
const TAB_KEYS = ACCESS_TABS.map(([key]) => key);

// Who may see which clients, and making people (administrators only), in three tabs: OVERVIEW (who can see what, and each person's access: Admin or Employee),
// EMPLOYEES (making a new employee, and the accounts: new link, deactivate) and CLIENT GROUPS. A company is a wall: an administrator sees and manages only their
// own company's people, groups and clients. A CLIENT GROUP is a named list of clients (or all of the company's), and a person sees the clients of every group
// they are in, plus any given to them one by one. A new person is made with no password: they get a one-time link to choose their own. The platform owner sees
// every company, makes new ones and places people in them, and is the only one who changes who is an Admin. None of the limits apply until they are switched on
// (DJANGO_CLIENT_ACCESS_ENFORCED=1 on the server); until then everyone signed in sees every client, as before.
export function Access({ title }) {
  const me = useAuth()?.user?.username;
  const [version, setVersion] = useState(0);
  const { data, error } = useApi(() => api.getAccess(), [version]);
  const { data: companies } = useApi(() => api.getCompanies(), [version]);
  const [tab, setTab] = usePersistentState('access:tab', 'overview', isOneOf(TAB_KEYS));   // remembered between visits
  const [state, setState] = useState({ busy: '', message: '', error: '' });
  const [notice, setNotice] = useState(null);                    // what to tell the administrator after a person was made, with the one-time link

  const run = async (busy, action, message) => {
    setState({ busy, message: '', error: '' });
    try { const result = await action(); setVersion((v) => v + 1); setState({ busy: '', message, error: '' }); return result ?? true; }
    catch (e) { setState({ busy: '', message: '', error: e.message }); return undefined; }
  };

  const clients = data?.clients ?? [];
  const groups = data?.client_groups ?? [];
  const owner = !!data?.owner;
  const myCompany = data?.company?.id ?? null;
  const ofCompany = (rows, id) => rows.filter((r) => (r.company ? r.company.id : null) === id);
  const shared = { data, companies: companies ?? [], groups, clients, owner, myCompany, me, state, run, ofCompany };

  return (
    <>
      <PageHeader title={title} range={false} />
      <ErrorNote error={error} />
      {data && (
        <p class={`note${data.enforced ? '' : ' error'}`} role="status">
          {data.enforced
            ? `Limits are ON. ${data.owner ? 'You are the platform owner and see every company.' : `You see ${data.company?.name ?? 'no company'} only.`} Admins see all of their own company's clients; employees see only the clients in their client groups, and any given to them one by one.`
            : 'Limits are OFF. Everyone signed in currently sees every client, whatever is listed below. Set up the groups and people first, then set DJANGO_CLIENT_ACCESS_ENFORCED=1 on the server and restart it.'}
        </p>
      )}
      {state.message && <p class="note" role="status">{state.message}</p>}
      {state.error && <p class="note error" role="alert">{state.error}</p>}
      {notice && <SetupNotice notice={notice} onDismiss={() => setNotice(null)} />}

      <div><Tabs tabs={ACCESS_TABS} value={tab} onChange={setTab} label="Sections of the Access page" prefix="access" /></div>   {/* in a block as wide as the page column, so the tabs line up with the heading and are not centred */}
      {data && tab === 'overview' && (
        <div {...tabPanelProps('access', 'overview')}>
          <OverviewPanel {...shared} />
          {owner && <CompaniesPanel companies={companies ?? []} state={state} run={run} />}
        </div>
      )}
      {data && tab === 'employees' && (
        <div {...tabPanelProps('access', 'employees')}>
          <NewEmployeePanel {...shared} setState={setState} setNotice={setNotice} bump={() => setVersion((v) => v + 1)} />
          <AccountsPanel {...shared} setNotice={setNotice} />
        </div>
      )}
      {data && tab === 'groups' && (
        <div {...tabPanelProps('access', 'groups')}>
          <GroupsPanel {...shared} />
        </div>
      )}
    </>
  );
}

// What happened when a person was made or given a fresh link, and the link itself (shown once: it is how they get in).
function SetupNotice({ notice, onDismiss }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { try { await navigator.clipboard.writeText(notice.link); setCopied(true); } catch { /* the box can be selected by hand */ } };
  return (
    <section class={`panel setup-notice ${notice.tone}`} role="status">
      <p>{notice.text}</p>
      <div class="toolbar">
        <input class="setup-link" readOnly value={notice.link} aria-label="Link to choose a password" onFocus={(e) => e.currentTarget.select()} />
        <button class="btn small" onClick={copy}>{copied ? 'Copied' : 'Copy Link'}</button>
        <button class="btn small" onClick={onDismiss}>Dismiss</button>
      </div>
      <p class="note">This link is shown only now. It lets whoever holds it choose a password for this account, so pass it on privately.</p>
    </section>
  );
}

// OVERVIEW: who can see what. Each person's company, access (Admin or Employee), client groups, and the clients given one by one. This is the purpose of the page,
// so it is first. Access is changed here by the platform owner only (the server refuses anyone else), and takes effect at once; groups and clients are saved with Save.
function OverviewPanel({ data, companies, groups, clients, me, owner, state, run, ofCompany }) {
  const [draftGroups, setDraftGroups] = useState({});        // username -> client group IDs as text, while being edited
  const [draftClients, setDraftClients] = useState({});      // username -> client IDs as text, while being edited
  useEffect(() => {
    setDraftGroups(Object.fromEntries(data.users.map((u) => [u.username, u.client_groups.map(String)])));
    setDraftClients(Object.fromEntries(data.users.map((u) => [u.username, u.clients.map(String)])));
  }, [data]);
  const dirty = (u) => !sameIds(draftGroups[u.username], u.client_groups) || !sameIds(draftClients[u.username], u.clients);
  const save = (u) => run(u.username, async () => {
    if (!sameIds(draftGroups[u.username], u.client_groups)) await api.updatePerson(u.username, { client_groups: (draftGroups[u.username] ?? []).map(Number) });
    if (!sameIds(draftClients[u.username], u.clients)) await api.saveAccess(u.username, (draftClients[u.username] ?? []).map(Number));
  }, `Saved what ${u.username} can see.`);
  return (
    <section class="panel">
      <div class="panel-head"><h2>Overview</h2><span class="count">{data.users.length} people</span></div>
      <h3>People And What They Can See</h3>
      <p class="note">An Admin sees every client of their company. An Employee sees the clients in their client groups, and any given to them one by one.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Person</th><th>Company</th><th>Access</th><th>Client Groups</th><th>Clients Given One By One</th><th /></tr></thead>
        <tbody>
          {data.users.map((u) => (
            <tr key={u.username} class={u.is_active ? '' : 'inactive'}>
              <td>
                <strong>{u.username}</strong>{u.name !== u.username && <div class="note">{u.name}</div>}{u.email && <div class="note">{u.email}</div>}
                {!u.is_active && <span class="badge bad">Deactivated</span>}
              </td>
              <td>
                {owner ? (
                  <select aria-label={`Company for ${u.username}`} value={u.company?.id ?? ''} disabled={state.busy === `co:${u.username}`}
                    onChange={(e) => run(`co:${u.username}`, () => api.setUserCompany(u.username, e.currentTarget.value ? Number(e.currentTarget.value) : null), `Moved ${u.username}.`)}>
                    <option value="">No company</option>
                    {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                ) : (u.company?.name ?? '—')}
              </td>
              <td>
                {u.is_owner || !data.can_make_admin || u.username === me ? (
                  <span title={u.is_owner ? 'The platform owner is above Admin and Employee.' : u.username === me ? 'You cannot change your own access.' : 'Only the platform owner can change who is an Admin.'}>{accessLabel(accessOf(u))}</span>
                ) : (
                  <select aria-label={`Access for ${u.username}`} value={accessOf(u)} disabled={state.busy === `acc:${u.username}`}
                    onChange={(e) => run(`acc:${u.username}`, () => api.updatePerson(u.username, accessRequest(e.currentTarget.value)), `${u.username} is now ${accessLabel(e.currentTarget.value)}.`)}>
                    {ACCESS_LEVELS.map(([level, label]) => <option key={level} value={level}>{label}</option>)}
                  </select>
                )}
              </td>
              <td class="wrap">
                {u.is_owner ? <span class="note">The platform owner sees everything.</span>
                  : u.is_staff ? <span class="note">Admins see every client of their company.</span>
                    : !u.company ? <span class="note">Not part of a company yet, so they see nothing.</span>
                      : <MultiFilter label="Groups" max={50} restrict emptyLabel="Add to a group" noneText="No client groups in this company yet."
                        value={draftGroups[u.username] ?? []} options={options(ofCompany(groups, u.company.id))} onChange={(v) => setDraftGroups({ ...draftGroups, [u.username]: asList(v) })} />}
              </td>
              <td class="wrap">
                {u.is_owner || u.is_staff || !u.company ? <span class="note">—</span>
                  : <MultiFilter label="Clients" max={500} restrict emptyLabel="Give a client" noneText="This company has no clients yet."
                    value={draftClients[u.username] ?? []} options={options(ofCompany(clients, u.company.id))} onChange={(v) => setDraftClients({ ...draftClients, [u.username]: asList(v) })} />}
              </td>
              <td class="row-actions">
                {!u.is_staff && u.company && <button class="btn small" disabled={!dirty(u) || state.busy === u.username} onClick={() => save(u)}>{state.busy === u.username ? 'Saving…' : 'Save'}</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </section>
  );
}

// Companies: the platform owner's. A company is a wall.
function CompaniesPanel({ companies, state, run }) {
  const [newCompany, setNewCompany] = useState('');
  return (
    <section class="panel">
      <div class="panel-head"><h2>Companies</h2><span class="count">{companies.length}</span></div>
      <p class="note">A company is a wall: its Admins and employees never see another company's clients. Only you, the platform owner, can make one.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Company</th><th class="num">People</th><th class="num">Clients</th></tr></thead>
        <tbody>{companies.map((c) => <tr key={c.id}><td><strong>{c.name}</strong></td><td class="num">{c.members}</td><td class="num">{c.clients}</td></tr>)}</tbody>
      </table></div>
      <div class="toolbar">
        <label class="field">New company<input value={newCompany} maxLength={200} onInput={(e) => setNewCompany(e.currentTarget.value)} /></label>
        <button class="btn" disabled={!newCompany.trim() || state.busy === 'company'} onClick={() => run('company', async () => { await api.createCompany(newCompany.trim()); setNewCompany(''); }, `Made the company ${newCompany.trim()}.`)}>Make Company</button>
      </div>
      <p class="note">Shared score weights and shared report templates are not yet separate for each company, so do not put a second organisation on this system until they are.</p>
    </section>
  );
}

// Making an employee: no password is chosen for them; they get a one-time link to choose their own.
function NewEmployeePanel({ data, companies, owner, myCompany, groups, ofCompany, state, setState, setNotice, bump }) {
  const [draft, setDraft] = useState(emptyPerson());
  const [touched, setTouched] = useState(false);
  const company = owner ? draft.company : String(myCompany ?? '');
  const companyId = company === '' ? null : Number(company);
  const problem = personProblem(draft, data.users.map((u) => u.username));
  const create = async () => {
    setState({ busy: 'person', message: '', error: '' });
    try {
      const result = await api.createPerson(personRequest({ ...draft, company: owner ? draft.company : '' }));
      setNotice(setupNotice(result, draft.first_name.trim()));
      setDraft(emptyPerson()); setTouched(false); bump();
      setState({ busy: '', message: `Made the account ${result.username}.`, error: '' });
    } catch (e) { setState({ busy: '', message: '', error: e.message }); }
  };
  return (
    <section class="panel">
      <div class="panel-head"><h2>New Employee</h2></div>
      <p class="note">Choose who they are and which client groups they belong to. No password is chosen for them: they are sent a link to choose their own.</p>
      <div class="toolbar">
        <label class="field">Username<input value={draft.username} maxLength={150} autoComplete="off" onInput={(e) => { setTouched(true); setDraft({ ...draft, username: e.currentTarget.value }); }} /></label>
        <label class="field">First Name<input value={draft.first_name} maxLength={150} onInput={(e) => setDraft({ ...draft, first_name: e.currentTarget.value })} /></label>
        <label class="field">Last Name<input value={draft.last_name} maxLength={150} onInput={(e) => setDraft({ ...draft, last_name: e.currentTarget.value })} /></label>
        <label class="field">Email<input type="email" value={draft.email} maxLength={254} autoComplete="off" onInput={(e) => { setTouched(true); setDraft({ ...draft, email: e.currentTarget.value }); }} /></label>
        {owner && (
          <label class="field">Company
            <select value={draft.company} onChange={(e) => setDraft({ ...draft, company: e.currentTarget.value, client_groups: [] })}>
              <option value="">Choose a company</option>
              {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
        )}
        {data.can_make_admin && (
          <label class="field">Access
            <select value={draft.access} onChange={(e) => setDraft({ ...draft, access: e.currentTarget.value })}>
              {ACCESS_LEVELS.map(([level, label]) => <option key={level} value={level}>{label}</option>)}
            </select>
          </label>
        )}
      </div>
      <MultiFilter label="Client Groups For The New Employee" max={50} restrict emptyLabel="Choose a client group" noneText={companyId ? 'This company has no client groups yet: make one in the Client Groups tab.' : 'Choose a company first.'}
        value={draft.client_groups} options={options(ofCompany(groups, companyId))} onChange={(v) => setDraft({ ...draft, client_groups: asList(v) })} />
      {touched && problem && <p class="note error" role="status">{problem}</p>}
      <div class="toolbar">
        <button class="primary" disabled={!!problem || companyId === null || state.busy === 'person'} onClick={create}>{state.busy === 'person' ? 'Making…' : 'Make Employee'}</button>
      </div>
    </section>
  );
}

// The accounts: who is active, a fresh link for someone whose first expired, and deactivating (never deleting).
function AccountsPanel({ data, me, state, run, setNotice }) {
  const link = async (u) => {
    const result = await run(`link:${u.username}`, () => api.newSetupLink(u.username), `Made a new link for ${u.username}.`);
    if (result && result !== true) setNotice(setupNotice(result, u.name !== u.username ? u.name : ''));
  };
  return (
    <section class="panel">
      <div class="panel-head"><h2>Employee Accounts</h2><span class="count">{data.users.length} people</span></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Person</th><th>Email</th><th>Access</th><th>Status</th><th /></tr></thead>
        <tbody>
          {data.users.map((u) => (
            <tr key={u.username} class={u.is_active ? '' : 'inactive'}>
              <td><strong>{u.username}</strong>{u.name !== u.username && <div class="note">{u.name}</div>}</td>
              <td>{u.email || '—'}</td>
              <td>{accessLabel(accessOf(u))}</td>
              <td><span class={`badge ${u.is_active ? 'good' : 'bad'}`}>{u.is_active ? 'Active' : 'Deactivated'}</span></td>
              <td class="row-actions">
                {u.is_active && !u.is_owner && <button class="btn small" aria-label={`New link for ${u.username}`} disabled={state.busy === `link:${u.username}`} onClick={() => link(u)}>New Link</button>}
                {u.username !== me && !u.is_owner && (
                  <button class="btn small" aria-label={`${u.is_active ? 'Deactivate' : 'Reactivate'} ${u.username}`} disabled={state.busy === `act:${u.username}`}
                    onClick={() => run(`act:${u.username}`, () => api.updatePerson(u.username, { is_active: !u.is_active }), u.is_active ? `Deactivated ${u.username}.` : `Brought ${u.username} back.`)}>{u.is_active ? 'Deactivate' : 'Reactivate'}</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
      <p class="note">A person is deactivated, never deleted: they are signed out at once and cannot sign in until brought back, and their reports and history stay.</p>
    </section>
  );
}

// Client groups: a named list of clients, or "all clients" (every client of the company, including ones made later).
function GroupsPanel({ groups, clients, companies, owner, myCompany, state, run, ofCompany }) {
  const [draft, setDraft] = useState(emptyGroup());
  const [deleting, setDeleting] = useState(null);
  const editing = !!draft.id;
  const company = owner ? draft.company : String(myCompany ?? '');
  const companyId = company === '' ? null : Number(company);
  const choices = options(ofCompany(clients, companyId));
  const save = async () => {
    const saved = await run('group', () => api.saveClientGroup(groupRequest({ ...draft, company })), editing ? `Saved ${draft.name.trim()}.` : `Made the group ${draft.name.trim()}.`);
    if (saved) setDraft(emptyGroup());
  };
  return (
    <section class="panel">
      <div class="panel-head"><h2>Client Groups</h2><span class="count">{groups.length}</span></div>
      <p class="note">A client group is a named list of clients, such as "Retail clients", or all of the company's clients. Put a person in one or more groups and they see the clients of all of them.</p>
      {groups.length === 0 ? <p class="note">There are no client groups yet.</p> : (
        <div class="table-wrap"><table>
          <thead><tr><th>Group</th>{owner && <th>Company</th>}<th>Clients</th><th class="num">People</th><th /></tr></thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.id}>
                <td><strong>{g.name}</strong></td>
                {owner && <td>{g.company?.name ?? '—'}</td>}
                <td class="wrap">{groupClients(g, clients)}</td>
                <td class="num" title={g.members.join(', ')}>{g.members.length}</td>
                <td class="row-actions">
                  {deleting === g.id ? (
                    <>
                      <span class="note">Delete {g.name}?</span>{' '}
                      <button class="btn small" disabled={state.busy === `del:${g.id}`} onClick={async () => { await run(`del:${g.id}`, () => api.deleteClientGroup(g.id), `Deleted ${g.name}.`); setDeleting(null); }}>Yes, Delete</button>
                      <button class="btn small" onClick={() => setDeleting(null)}>Keep</button>
                    </>
                  ) : (
                    <>
                      <button class="btn small" aria-label={`Edit ${g.name}`} onClick={() => setDraft({ id: g.id, name: g.name, all_clients: g.all_clients, clients: g.clients.map(String), company: String(g.company?.id ?? '') })}>Edit</button>
                      <button class="btn small" aria-label={`Delete ${g.name}`} onClick={() => setDeleting(g.id)}>Delete</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      <h3>{editing ? `Edit ${draft.name || 'group'}` : 'New Group'}</h3>
      <div class="toolbar">
        <label class="field">Group Name<input value={draft.name} maxLength={80} placeholder="Retail clients" onInput={(e) => setDraft({ ...draft, name: e.currentTarget.value })} /></label>
        {owner && !editing && (
          <label class="field">Company
            <select value={draft.company} onChange={(e) => setDraft({ ...draft, company: e.currentTarget.value, clients: [] })}>
              <option value="">Choose a company</option>
              {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
        )}
        <label class="check"><input type="checkbox" checked={draft.all_clients} onChange={(e) => setDraft({ ...draft, all_clients: e.currentTarget.checked })} />All clients</label>
      </div>
      {!draft.all_clients && (
        <MultiFilter label="Clients In The Group" max={500} restrict emptyLabel="Choose a client" noneText={companyId ? 'This company has no clients yet.' : 'Choose a company first.'}
          value={draft.clients} options={choices} onChange={(v) => setDraft({ ...draft, clients: asList(v) })} />
      )}
      {draft.all_clients && <p class="note">Everyone in this group sees every client of the company, including clients made later.</p>}
      <div class="toolbar">
        <button class="primary" disabled={!draft.name.trim() || companyId === null || state.busy === 'group'} onClick={save}>{editing ? 'Save Group' : 'Make Group'}</button>
        {editing && <button class="btn" onClick={() => setDraft(emptyGroup())}>Cancel</button>}
      </div>
    </section>
  );
}
