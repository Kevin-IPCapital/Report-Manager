import { useState } from 'preact/hooks';
import { api } from '../api/adapter';
import { PageHeader } from '../components/PageHeader';
import { StatCard, ErrorNote } from '../components/ui';
import { useApi } from '../components/useApi';
import { gapText, openDays, shortDate, sinceText } from '../gaps';

// Customers found in the source systems that no client is linked to yet are grouped by name: the same words in the same order
// (capitals and punctuation ignored) means the same group. That is an inference, so every group says so and nothing is linked
// until a person confirms it; confirming links each customer by its own ID. Below that, clients missing from a source.
export function ClientResolution({ title }) {
  const [version, setVersion] = useState(0);
  const { data, error } = useApi(() => api.getResolution(), [version]);
  const { data: clients } = useApi(() => api.getClients(), [version]);
  const [edits, setEdits] = useState({});            // proposal key -> { name, clientId }
  const [state, setState] = useState({ busy: false, message: '', errors: [] });

  const label = Object.fromEntries((data?.sources ?? []).map((s) => [s.key, s.label]));
  const edit = (p) => edits[p.key] ?? {};
  const clientId = (p) => edit(p).clientId ?? p.existing_client_id ?? '';
  const payload = (p) => ({ name: edit(p).name ?? p.name, client_id: clientId(p) || undefined, members: p.members });
  const setEdit = (p, patch) => setEdits({ ...edits, [p.key]: { ...edit(p), ...patch } });

  const resolve = async (proposals) => {
    setState({ busy: true, message: '', errors: [] });
    try {
      const r = await api.resolveClients(proposals.map(payload));
      setState({ busy: false, message: `Created ${r.created} client(s) and added to ${r.extended} existing client(s).`, errors: r.errors });
      setEdits({});
      setVersion((v) => v + 1);
    } catch (e) { setState({ busy: false, message: '', errors: [{ group: '', error: e.message }] }); }
  };
  const acceptAll = () => {
    const n = data.proposals.length;
    if (window.confirm(`Create or extend clients for all ${n} group(s) from names alone? Each customer is linked by its ID. You can edit or unlink them afterwards on the Clients page.`)) resolve(data.proposals);
  };

  const withIssues = (data?.clients ?? []).filter((c) => c.discrepancies.length);
  const unreadable = (data?.sources ?? []).filter((s) => !s.available);

  return (
    <>
      <PageHeader title={title} range={false} />
      <ErrorNote error={error} />
      {data && (
        <>
          <div class="stats">
            <StatCard label="Clients" value={data.summary.clients} />
            <StatCard label="Customers Not Linked" value={data.summary.unlinked_customers} status={data.summary.unlinked_customers ? 'warn' : 'good'} />
            <StatCard label="Groups Inferred From Names" value={data.summary.inferred} status={data.summary.inferred ? 'warn' : 'good'} />
            <StatCard label="Open Gaps" value={data.summary.open_gaps} status={data.summary.open_gaps ? 'warn' : 'good'} />
            <StatCard label="Clients With Discrepancies" value={data.summary.clients_with_discrepancies} status={data.summary.clients_with_discrepancies ? 'bad' : 'good'} />
          </div>
          {unreadable.length > 0 && (
            <p class="note" role="status">Could not read the customer list for {unreadable.map((s) => s.label).join(', ')}, so those sources are not counted in the messages below.</p>
          )}
          {state.message && <p class="note" role="status">{state.message}</p>}
          {state.errors.length > 0 && <ul class="note error" role="alert">{state.errors.map((e, i) => <li key={i}>{e.group ? `${e.group}: ` : ''}{e.error}</li>)}</ul>}

          <section class="panel">
            <div class="panel-head">
              <h2>Customers Not Linked To A Client</h2>
              <div class="panel-actions">
                <span class="count">{data.proposals.length} group(s)</span>
                {data.proposals.length > 0 && <button class="btn small" disabled={state.busy} onClick={acceptAll}>Accept All</button>}
              </div>
            </div>
            <p class="note">Customers are grouped when their names have exactly the same words in the same order (capitals and punctuation are ignored). One extra or
              different word keeps them apart. This is an inference from names, so check each group before confirming.</p>
            {data.proposals.length === 0 ? <p class="note">Every customer found is linked to a client.</p> : (
              <div class="table-wrap"><table>
                <thead><tr><th>Customers</th><th>Create Or Link</th><th>Notes</th><th /></tr></thead>
                <tbody>
                  {data.proposals.map((p) => (
                    <tr key={p.key}>
                      <td>{p.members.map((m) => <div key={`${m.source}:${m.external_id}`}>{label[m.source]} {m.external_id}: {m.customer_name ?? '(no name)'}</div>)}</td>
                      <td>
                        <select value={clientId(p)} onChange={(e) => setEdit(p, { clientId: e.target.value })} aria-label="Client to link to">
                          <option value="">Create a new client…</option>
                          {(clients ?? []).map((c) => <option key={c.id} value={c.id}>Add to {c.name}</option>)}
                        </select>
                        {!clientId(p) && <input class="inline-input" aria-label="New client name" value={edit(p).name ?? p.name} onInput={(e) => setEdit(p, { name: e.target.value })} />}
                      </td>
                      <td class="wrap">
                        {p.inferred && <span class="badge warn">Inference</span>}
                        {p.message && <div>{p.message}</div>}
                        {p.warnings.map((w) => <div key={w} class="note error">{w}</div>)}
                        {p.discrepancy && <div class="note">{p.discrepancy}</div>}
                        {p.since && <div class="note">Not linked {sinceText(p.since)}.</div>}
                      </td>
                      <td><button class="btn small" disabled={state.busy} onClick={() => resolve([p])}>{clientId(p) ? 'Link' : 'Create'}</button></td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            )}
          </section>

          <section class="panel">
            <div class="panel-head"><h2>Data Discrepancies</h2><span class="count">{withIssues.length} client(s)</span></div>
            {withIssues.length === 0 ? <p class="note">No discrepancies: every active client is linked in every source that could be read, and no link points at a missing customer.</p> : (
              <div class="table-wrap"><table>
                <thead><tr><th>Client</th><th>Linked In</th><th>Problems</th></tr></thead>
                <tbody>
                  {withIssues.map((c) => (
                    <tr key={c.id}>
                      <td>{c.name}</td>
                      <td>{c.linked_in.map((s) => label[s]).join(', ') || 'None'}</td>
                      <td class="wrap">{c.discrepancies.map((d, i) => <div key={d}>{d}{c.since?.[i] && <span class="note"> {sinceText(c.since[i])}.</span>}</div>)}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            )}
          </section>

          <section class="panel">
            <div class="panel-head"><h2>Recently Resolved</h2><span class="count">{(data.recently_resolved ?? []).length} in the last 30 days</span></div>
            {(data.recently_resolved ?? []).length === 0 ? <p class="note">Nothing has been resolved in the last 30 days.</p> : (
              <div class="table-wrap"><table>
                <thead><tr><th>Gap</th><th>Client Or Customer</th><th>First Seen</th><th>Resolved</th><th>Open For</th></tr></thead>
                <tbody>
                  {data.recently_resolved.map((g, i) => (
                    <tr key={`${g.kind}-${g.source}-${g.external_id}-${g.resolved_at}-${i}`}>
                      <td>{gapText(g)}</td><td>{g.label}</td><td>{shortDate(g.first_seen_at)}</td><td>{shortDate(g.resolved_at)}</td>
                      <td>{openDays(g.first_seen_at, g.resolved_at)} day(s)</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            )}
          </section>
        </>
      )}
    </>
  );
}
