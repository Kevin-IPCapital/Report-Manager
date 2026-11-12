import { useEffect, useState } from 'preact/hooks';
import { api } from '../api/adapter';
import { PageHeader } from '../components/PageHeader';
import { basisLabel, failureNote, freshnessView } from '../freshness';
import { formatDuration } from '../text';

const NAMES = {
  ncentral_inventory: 'N-central · Inventory',
  ncentral_patches: 'N-central · Patches',
  ncentral_monitoring: 'N-central · Monitoring',
  autotask_tickets: 'Autotask · Tickets',
  sentinelone_monthly: 'SentinelOne · Monthly',
  cove_backup: 'Cove · Backup',
};

const STATUS_CLASS = {
  SUCCESS: 'good', PARTIAL: 'warn', FAILED: 'bad',
  STARTED: 'warn', NEVER_RUN: '',
};

function stamp(iso) {
  if (!iso) return 'Never';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? 'Unknown' : date.toLocaleString();
}

function duration(value) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toFixed(2)}s` : '—';
}

// How recently the worker ran, from its heartbeat. The server decides whether that is too long (see the alerts).
function workerText(heartbeat) {
  if (!heartbeat?.last_tick_at) return 'Worker: has not run yet. Start the scheduled task (see backend/deploy/README.md).';
  return `Worker last ran ${formatDuration(heartbeat.age_seconds / 3600)} ago (${heartbeat.state.toLowerCase()}).`;
}

// Freshness is judged by the server from the last run that succeeded or completed partly, never from a failure.
function FreshnessCell({ job }) {
  const f = freshnessView(job);
  const note = failureNote(f);
  return (
    <>
      <span title={f.staleSince ? `Went stale ${stamp(f.staleSince)}` : undefined}>{f.text}{f.stale ? ' · Stale' : ''}</span>
      {f.at && <span class="collection-secondary" title={basisLabel(f)}> · {f.basis === 'last good run' ? 'last good run ' : 'stored data '}{stamp(f.at)}</span>}
      {note && <div class="collection-secondary">{note}</div>}
    </>
  );
}

export function CollectionHealth({ title = 'Data Collection' }) {
  const [result, setResult] = useState({ data: null, loading: true, error: '' });
  const [refreshCount, setRefreshCount] = useState(0);
  const [selectedJob, setSelectedJob] = useState(null);
  const [execution, setExecution] = useState({ active: false, batch: null, schedules: {} });
  const [executionError, setExecutionError] = useState('');
  const [queuePending, setQueuePending] = useState(false);
  const [clock, setClock] = useState(Date.now());
  const [busyRefresh, setBusyRefresh] = useState(0);
  useEffect(() => {
    let alive = true;
    const update = () => {
      api.getCollectionExecution().then(data => { if (alive) { setExecution(data); setExecutionError(''); } })
        .catch(err => { if (alive) setExecutionError(err.message); });
      setClock(Date.now());
    };
    update();
    const timer = setInterval(update, 2500);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (!execution.active && execution.batch?.finished_at) {
      setRefreshCount(x => x + 1);
    }
  }, [execution.batch?.id, execution.batch?.finished_at]);
  async function run(job) {
    if (execution.active || queuePending) return;
    setQueuePending(true);
    setExecutionError('');
    try {
      await api.startCollection(job);
      const status = await api.getCollectionExecution();
      setExecution(status);
    } catch (e) { setExecutionError(e.message); }
    finally { setQueuePending(false); }
  }
  const locked = execution.active || queuePending;
  function nextRun(job) {
    const item = execution.schedules?.[job];
    if (!item?.schedule_enabled) return 'Manual only';
    if (!item.next_execution_at) return 'Due on next worker tick';
    const delta = new Date(item.next_execution_at).getTime() - clock;
    if (!Number.isFinite(delta)) return 'Unknown';
    if (delta <= 0) return `Due now · ${stamp(item.next_execution_at)}`;
    const mins = Math.ceil(delta / 60000);
    return `${mins >= 60 ? `${Math.floor(mins / 60)}h ` : ''}${mins % 60}m · ${stamp(item.next_execution_at)}`;
  }
  const [logResult, setLogResult] = useState({ log: '', loading: false, error: '', available: false, truncated: false });
  useEffect(() => {
    if (!selectedJob) return;
    let active = true;
    setLogResult({ log: '', loading: true, error: '', available: false, truncated: false });
    api.getCollectorLatestLog(selectedJob)
      .then((data) => { if (active) setLogResult({ ...data, loading: false, error: '' }); })
      .catch((err) => { if (active) setLogResult({ log: '', loading: false, error: err.message, available: false, truncated: false }); });
    return () => { active = false; };
  }, [selectedJob, refreshCount]);
  useEffect(() => {
    let active = true;
    setResult((previous) => ({ ...previous, loading: true, error: '' }));
    api.getCollectionHealth()
      .then((data) => active && setResult({ data, loading: false, error: '' }))
      .catch((error) => active && setResult((previous) => ({
        ...previous, loading: false,
        error: error.status === 403 ? 'The backend is still restricting this endpoint. This account is not staff. Run `python manage.py makestaff <username>` on the backend (or, for local development only, set DJANGO_DEV_COLLECTOR_HEALTH_FOR_ALL=1 in backend/.env).' : error.message,
      })));
    return () => { active = false; };
  }, [refreshCount]);

  const jobs = result.data?.jobs ?? [];
  const alerts = result.data?.alerts ?? [];
  return (
    <>
      <PageHeader title={title} range={false} />
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>Collector health</h2>
            <p class="note collection-subtitle">View collection results, schedule timings and queue a manual update.</p>
          </div>
          <div class="collection-actions">
            <button type="button" class="btn collection-run" disabled={locked || !jobs.some(j => j.enabled)} onClick={() => run('all')}>{queuePending ? 'Queueing…' : 'Update all'}</button>
          <button type="button" class="btn" disabled={result.loading}
            onClick={() => setRefreshCount((count) => count + 1)}>
            {result.loading ? 'Loading…' : 'Refresh status'}
          </button></div>
        </div>
        {executionError && <p role="alert" class="note error">{executionError}</p>}
        <p class="note collection-worker">{workerText(execution.heartbeat)}</p>
        {execution.batch && <section class="collection-progress" aria-live="polite">
          <div class="collection-progress-line">
            <strong>{execution.active ? (execution.batch.status === 'QUEUED' ? 'Queued for worker' : 'Collection running') : `Latest batch: ${execution.batch.status}`}</strong>
            <span>{execution.batch.completed} / {execution.batch.total} collectors complete
              {execution.batch.current_job ? ` · ${NAMES[execution.batch.current_job] || execution.batch.current_job} running` : ''}</span>
          </div>
          <progress class={execution.active ? 'collection-progress-active' : ''} max={Math.max(1, execution.batch.total)} value={execution.batch.completed} />
          <span class="note collection-progress-label">{execution.active ? 'Updates after each collector finishes. The active collector has no per-record progress feed.' : 'Last batch finished. Status and logs can be refreshed.'}</span>
        </section>}
        {result.error && <p role="alert" class="note error">{result.error}</p>}
        {!result.data && result.loading && <p role="status" class="note">Loading collection status…</p>}
        {result.data && (
          <>
            {!result.data.configured && <p role="alert" class="note error">Collector configuration could not be loaded. Copy backend/collector_jobs.example.json to backend/collector_jobs.json and adjust it.</p>}
            {alerts.length > 0 && (
              <ul class="collection-alert collection-alerts" role="status" aria-label="Collection problems">
                {alerts.map((a) => <li key={a.key} class={a.severity}>{a.message}</li>)}
              </ul>
            )}
            <p class="note collection-updated">Status checked: {stamp(result.data.checked_at)}</p>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Collector</th><th scope="col">Enabled</th>
                    <th scope="col">Latest outcome</th><th scope="col">Last run</th>
                    <th scope="col">Duration</th><th scope="col">Data freshness</th><th scope="col">Next execution</th><th scope="col">Run now</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((job) => (
                    <tr key={job.job}>
                      <td><button type="button" class="collection-job-button" aria-expanded={selectedJob === job.job}
                        onClick={() => setSelectedJob((previous) => previous === job.job ? null : job.job)}>
                        {NAMES[job.job] || job.job} <span aria-hidden="true">{selectedJob === job.job ? '▴' : '▾'}</span>
                      </button></td>
                      <td>{job.enabled ? 'Yes' : 'No'}</td>
                      <td><span class={`badge ${STATUS_CLASS[job.status] || ''}`} title={job.status === 'PARTIAL' && job.disconnected_devices != null ? `${job.disconnected_devices} device${job.disconnected_devices === 1 ? '' : 's'} could not be found in N-central. They count as disconnected, and say so on Server Health.` : undefined}>
                        {job.status === 'STARTED' ? 'Started / incomplete' : job.status === 'NEVER_RUN' ? 'Never run' : job.status === 'PARTIAL' ? 'Partial' : job.status}
                      </span></td>
                      <td>{stamp(job.last_run_at)}</td>
                      <td>{duration(job.last_duration_seconds)}</td>
                      <td><FreshnessCell job={job} /></td>
                      <td>{nextRun(job.job)}</td>
                      <td><button type="button" class="btn collection-run" disabled={locked || !job.enabled} onClick={() => run(job.job)} title={job.enabled ? `Queue ${job.job}` : 'Collector disabled in configuration'}>Rerun</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {selectedJob && <section class="collection-log" aria-live="polite">
              <div class="panel-head"><h3>Latest run log — {NAMES[selectedJob] || selectedJob}</h3>
                <button type="button" class="btn" onClick={() => setSelectedJob(null)}>Close</button></div>
              {logResult.loading && <p class="note">Loading latest run…</p>}
              {logResult.error && <p class="note error" role="alert">{logResult.error}</p>}
              {!logResult.loading && !logResult.error && !logResult.available && <p class="note">No log is available for this collector.</p>}
              {!logResult.loading && logResult.available && <>
                {logResult.truncated && <p class="note">Showing a truncated log; older lines were omitted.</p>}
                <pre class="collection-log-text">{logResult.log}</pre>
              </>}
            </section>}
            <p class="note collection-footnote">Data freshness is judged from the last run that succeeded or completed partly: a failed run, or a dry run, never makes data look fresh and never hides that it has gone stale. “Latest outcome” and “Last run” show the most recent run, including a failure, and the failed runs since the last good one are counted under the freshness. If the log has no good run yet, the newest stored timestamp is used and labelled as such.</p>
          </>
        )}
      </section>
    </>
  );
}
