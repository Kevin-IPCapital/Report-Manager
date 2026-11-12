// What the Data Collection page says about how fresh a job's data is. Freshness is judged by the server from the last run that
// succeeded or completed partly (never from a failure); this only turns that into words. When the log has no good run, the newest
// stored timestamp is shown instead, and says so.
import { formatDuration } from './text.js';

export function freshnessView(job) {
  const failures = job.failures_since_good ?? 0;
  if (job.last_good_at) {
    return { text: `${formatDuration(job.good_run_age_hours)} ago`, stale: job.freshness === 'STALE', basis: 'last good run', at: job.last_good_at, staleSince: job.stale_since ?? null, failures };
  }
  if (job.data_age_hours != null) {
    return { text: `${formatDuration(job.data_age_hours)} ago`, stale: job.freshness === 'STALE', basis: 'stored data', at: job.data_as_of ?? null, staleSince: null, failures };
  }
  return { text: 'No successful run yet', stale: false, basis: 'none', at: null, staleSince: null, failures };
}

// "3 failed runs since", or, when nothing has ever succeeded, "3 failed runs, none succeeded". Empty when there were none.
export function failureNote(view) {
  if (!view.failures) return '';
  const runs = `${view.failures} failed run${view.failures === 1 ? '' : 's'}`;
  return view.basis === 'none' ? `${runs}, none succeeded` : `${runs} since`;
}

// The "data as of" line under a page's heading: when its data was last collected, and whether that is as recent as the schedule expects.
// `source` is one entry of GET /api/freshness; `stamp` words a timestamp (a parameter so tests need no particular time zone).
const defaultStamp = (iso) => {
  const time = Date.parse(iso);
  return Number.isNaN(time) ? 'an unknown time' : new Date(time).toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
};

export function asOfView(source, stamp = defaultStamp) {
  if (!source.as_of) {
    return { tone: 'warn', text: `${source.label}: no data collected yet${source.enabled ? '' : ' (the collector is switched off)'}`, title: 'Nothing has been collected from this source yet.' };
  }
  const when = `${source.label} as of ${stamp(source.as_of)} (${formatDuration(source.age_hours)} ago)`;
  const from = source.basis === 'stored data' ? 'From the newest row stored (there is no successful collection in the log).' : 'From the last collection that succeeded.';
  if (source.state === 'stale') {
    const n = source.failures_since;
    const why = n > 0 ? `the last ${n === 1 ? '' : `${n} `}collection${n === 1 ? '' : 's'} failed` : 'no collection has run since';
    return { tone: 'bad', text: `${when}. Stale: ${why}`, title: `${from} It is older than the collector's schedule expects.` };
  }
  if (source.state === 'current') return { tone: 'good', text: when, title: `${from} It is as recent as the collector's schedule expects.` };
  return { tone: '', text: when, title: `${from} How recent it should be is not known: the collector has no schedule.` };
}

export const basisLabel = (view) => ({ 'last good run': 'last good run', 'stored data': 'stored data (no good run in the log)', none: '' }[view.basis]);
