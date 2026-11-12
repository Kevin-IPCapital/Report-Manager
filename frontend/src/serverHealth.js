// Server Health: which endpoints count as connected, what each filter tile selects, and what the tiles count. Pure, so it can be tested.
import { matchesDevice } from './filter.js';
import { shortDate } from './gaps.js';

// The values come from the data, so the network status is read loosely.
export function connectionOf(status) {
  const s = String(status ?? '');
  if (/dis|off|down/i.test(s)) return 'disconnected';
  if (/connect|online|up/i.test(s)) return 'connected';
  return 'unknown';
}
export const CONNECTION_TONE = { connected: 'good', disconnected: 'bad', unknown: 'warn' };

// There are two connection statuses, one from each system. SentinelOne's is its agent's network status. N-central's is whether it can find the device,
// judged from its newest patch collection (NC-01): connected when that worked, disconnected when N-central answered 404 (device not found). A server with
// no matching N-central device has no N-central status, so it is in neither N-central tile. (The first four tiles' keys are what is remembered between visits.)
export const ncentralState = (s) => (s.patches?.status === 'matched' ? s.patches.collection?.state ?? null : null);

// The tiles that filter. `excludes` is the filter that cannot be on at the same time.
export const TILE_FILTERS = {
  servers: { label: 'Servers', test: (s) => String(s.machine_type ?? '').toLowerCase() === 'server' },
  connected: { label: 'SentinelOne Connected', test: (s) => connectionOf(s.network_status) === 'connected', excludes: 'disconnected' },
  disconnected: { label: 'SentinelOne Disconnected', test: (s) => connectionOf(s.network_status) === 'disconnected', excludes: 'connected' },
  ncConnected: { label: 'N-central Connected', test: (s) => ncentralState(s) === 'ok', excludes: 'ncDisconnected' },
  ncDisconnected: { label: 'N-central Disconnected', test: (s) => ncentralState(s) === 'disconnected', excludes: 'ncConnected' },
  infected: { label: 'Infected', test: (s) => s.infected === true },
  outdated: { label: 'Out Of Date', test: (s) => s.is_up_to_date === false },
};

// Turn one tile filter on or off. Turning on connected turns off disconnected, and the other way round.
export function toggleFilter(active, key) {
  const { [key]: wasOn, ...rest } = active;
  if (wasOn) return rest;
  const { [TILE_FILTERS[key].excludes]: _dropped, ...kept } = rest;
  return { ...kept, [key]: true };
}

export const applyFilters = (rows, active, device = '') => rows.filter((s) => Object.keys(active).every((k) => !active[k] || TILE_FILTERS[k].test(s)) && matchesDevice(device, s.computer_name));

// What the tiles show, for the rows that match. Unknown values (a missing infected flag, an unknown status) are not counted as bad.
export function tileCounts(rows) {
  const count = (test) => rows.filter(test).length;
  return { endpoints: rows.length, ...Object.fromEntries(Object.entries(TILE_FILTERS).map(([key, f]) => [key, count(f.test)])) };
}

// ---- patching on a card. `patches` is the server's `patches` from the API: {status, device_name, categories}.
const NOTES = {
  'no-match': 'No N-central device with this name was found in this client, so there is no patch data.',
  ambiguous: 'More than one N-central device has this name in this client, so none is used.',
  unlinked: 'This server is not linked to a client, so it cannot be matched to an N-central device.',
  unavailable: 'Patch data could not be read just now.',
};

export const daysAgo = (days) => (days == null ? '' : days < 1 ? 'today' : `${Math.floor(days)} day${Math.floor(days) === 1 ? '' : 's'} ago`);

// What a card says about patching, in the three Overview categories. For each: when a patch was last added, when the next is due (not known:
// the schedule is not collected), and whether it is up to date, meaning nothing is missing. What "healthy" means for a whole server is not
// decided, so none is shown. A flag marks a category where something has been missing for longer than the grace period.
export function patchView(patches, graceDays = 14) {
  if (!patches || patches.status !== 'matched') return { matched: false, note: NOTES[patches?.status] ?? NOTES.unavailable };
  const days = (n) => `${Math.floor(n)} day${Math.floor(n) === 1 ? '' : 's'}`;
  const rows = patches.categories.map((c) => {
    const flag = c.overdue ? 'overdue' : c.missing > 0 ? 'missing' : '';
    const oldest = c.oldest_missing_days == null ? '' : `, the oldest for ${days(c.oldest_missing_days)}`;
    const upTitle = c.up_to_date == null ? 'No patches of this kind are listed for this server, so nothing can be said.'
      : c.up_to_date ? 'Nothing is missing in this category.'
        : `${c.missing} patch${c.missing === 1 ? ' is' : 'es are'} missing${oldest}${c.overdue ? `: more than the ${graceDays} days allowed, so one that was expected has not been applied.` : c.oldest_missing_days == null ? '.' : `, which is within the ${graceDays} days allowed.`}`;
    return {
      key: c.key, label: c.label,
      last: c.last_installed_at ? `${shortDate(c.last_installed_at)} (${daysAgo(c.days_since_last)})` : 'No dated install',
      lastDate: c.last_installed_at ? shortDate(c.last_installed_at) : 'No dated install', lastAgo: c.last_installed_at ? daysAgo(c.days_since_last) : '',   // the same, in two lines for a narrow card
      next: c.next_due ? shortDate(c.next_due) : 'Not collected',
      nextTitle: 'The patch schedule is not collected from N-central yet, so the next patch due is not known.',
      upToDate: c.up_to_date ?? null, upTitle, flag, flagText: flag === 'overdue' ? 'Overdue' : flag === 'missing' ? `${c.missing} missing` : '',
    };
  });
  return { matched: true, device: patches.device_name, rows, collection: collectionView(patches) };
}

// ---- a device N-central cannot find. Its patch collection answered 404, which counts as disconnected (NC-01, decided 12 Nov 2026), and the card
// says so. Its figures are from the last SUCCESSFUL inventory, so the note gives that date.
export function collectionView(patches) {
  const state = patches?.collection?.state ?? null;
  const last = patches?.inventory ? `${shortDate(patches.inventory.at)} (${daysAgo(patches.inventory.days_ago)})` : null;
  const tail = last ? `The figures below are from its last successful inventory, ${last}.` : 'No successful inventory has been recorded for it.';
  const when = patches?.collection?.attempted_at ? shortDate(patches.collection.attempted_at) : 'the latest one';
  if (state === 'disconnected') return { state, label: 'Disconnected', tone: 'bad', note: `N-central cannot find this device: its latest patch collection (${when}) answered 404, so it counts as disconnected. ${tail}` };
  if (state === 'failed') return { state, label: 'Collection failed', tone: 'warn', note: `N-central could not read this device's patches in its latest patch collection (${when}). ${tail}` };
  if (state === null) return { state, label: '', tone: '', note: 'No patch collection has been recorded for this device yet.' };
  return { state, label: '', tone: '', note: '' };
}

// ---- the three sections of a card, in order; each opens and closes on its own heading
export const CARD_SECTIONS = [
  { key: 'performance', title: 'Performance' },
  { key: 'patches', title: 'Patches' },
  { key: 'collected', title: 'Data Collected' },
];

// ---- Data Collected: the latest date found for this device on a successful run of each collector
const COLLECTED_TEXT = {
  none: 'No date recorded',
  'no-match': 'No matching device in this client',
  ambiguous: 'More than one device has this name',
  unlinked: 'This server is not linked to a client',
  'not-collected': 'Nothing collected yet',
  unavailable: 'Could not be read just now',
};

export function collectedView(collected) {
  return (collected ?? []).map((i) => ({
    key: i.key, source: i.label, what: i.what, state: i.state, found: i.state === 'found',
    text: i.state === 'found' ? `${shortDate(i.at)} (${daysAgo(i.days_ago)})` : COLLECTED_TEXT[i.state] ?? COLLECTED_TEXT.unavailable,
    date: i.state === 'found' ? shortDate(i.at) : COLLECTED_TEXT[i.state] ?? COLLECTED_TEXT.unavailable, ago: i.state === 'found' ? daysAgo(i.days_ago) : '',   // the same, in two lines for a narrow card
    title: i.at ? `Captured ${new Date(i.at).toLocaleString()}` : '',
  }));
}

// ---- the N-central connection status on a card. `key` is connected, disconnected, failed, never (matched, but its patches have never been collected) or
// unknown (no N-central device matches this server, so nothing can be said); `tone` is the badge colour and `title` says why.
export function ncentralStatus(patches) {
  if (!patches || patches.status !== 'matched') {
    return { key: 'unknown', label: 'Unknown', tone: '', title: NOTES[patches?.status] ?? NOTES.unavailable };
  }
  const state = patches.collection?.state ?? null;
  const view = collectionView(patches);
  if (state === 'ok') return { key: 'connected', label: 'Connected', tone: 'good', title: 'N-central found this device in its newest patch collection.' };
  if (state === 'disconnected') return { key: 'disconnected', label: 'Disconnected', tone: 'bad', title: view.note };
  if (state === 'failed') return { key: 'failed', label: 'Collection failed', tone: 'warn', title: view.note };
  return { key: 'never', label: 'Not collected', tone: '', title: view.note };
}
