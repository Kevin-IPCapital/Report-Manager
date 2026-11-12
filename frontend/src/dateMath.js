// Small date helpers on local dates written YYYY-MM-DD (toISOString would shift the day in other time zones).
const pad = (n) => String(n).padStart(2, '0');
export const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parts = (s) => s.split('-').map(Number);

// N months before an ISO date, clamped to the shorter month (31 Mar - 1 month = 28/29 Feb).
export function subMonths(isoDate, n) {
  const [y, m, d] = parts(isoDate);
  return iso(new Date(y, m - 1 - n, Math.min(d, new Date(y, m - n, 0).getDate())));
}
export function addDays(isoDate, n) {
  const [y, m, d] = parts(isoDate);
  return iso(new Date(y, m - 1, d + n));
}
export const daysBetween = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000);

// A real calendar day in YYYY-MM-DD form ("2026-02-30" is not).
export function isIsoDay(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [y, m, d] = parts(value);
  return iso(new Date(y, m - 1, d)) === value;
}

// The first day of "the last N days / weeks / months", ending on `today` (YYYY-MM-DD) and counting it: the last 30 days is today and the 29 before
// it; the last 3 months starts the day after the same date 3 months back (on 10 Nov it runs from 11 Aug). The one place this is worked out.
export function durationStart(last, today) {
  return last.unit === 'months' ? addDays(subMonths(today, last.amount), 1) : addDays(today, -(last.amount * (last.unit === 'weeks' ? 7 : 1) - 1));
}
