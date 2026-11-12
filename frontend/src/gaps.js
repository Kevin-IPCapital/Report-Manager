// How the Client Resolution page words the record of gaps. Pure, so it can be tested.
export const KIND_TEXT = {
  unlinked: (g) => `${g.source_label} customer not linked to a client`,
  missing: (g) => `Client missing from ${g.source_label}`,
  stale: (g) => `Link to ${g.source_label} ${g.external_id} no longer exists`,
};

export const gapText = (g) => (KIND_TEXT[g.kind] ?? (() => 'Gap'))(g);

// "5 Nov 2026" for a date from the record, or '' when there is none.
export function shortDate(iso) {
  const t = Date.parse(iso ?? '');
  return Number.isNaN(t) ? '' : new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

// "since 5 Nov 2026" (or nothing when nobody knows).
export const sinceText = (iso) => (shortDate(iso) ? `since ${shortDate(iso)}` : '');

// How long a gap stayed open, in whole days ("0 days" for one closed the same day).
export function openDays(firstIso, resolvedIso) {
  const a = Date.parse(firstIso ?? ''), b = Date.parse(resolvedIso ?? '');
  return Number.isNaN(a) || Number.isNaN(b) ? null : Math.max(0, Math.round((b - a) / 86400000));
}
