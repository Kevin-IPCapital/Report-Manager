// Trend ranges and the ticket volume trend. Pure, so they can be tested.
import { addDays, daysBetween, durationStart } from './dateMath.js';

// The ranges a person can choose for the patch score history. "All history" shows everything saved.
// Each is a length (counted the same way as every other "last N months", see dateMath.durationStart), so a range you save yourself behaves
// exactly like these. The ranges you have saved are added to the choices as `saved:<name>`.
export const TREND_RANGES = [
  { key: 'all', label: 'All history', last: null },
  { key: '3m', label: 'Last 3 months', last: { amount: 3, unit: 'months' } },
  { key: '6m', label: 'Last 6 months', last: { amount: 6, unit: 'months' } },
  { key: '12m', label: 'Last 12 months', last: { amount: 12, unit: 'months' } },
];
export const TREND_RANGE_KEYS = TREND_RANGES.map((r) => r.key);
// May this be the remembered choice? A built-in key, or a saved range's (whether it still exists is decided when it is used).
export const isTrendRangeKey = (v) => typeof v === 'string' && (TREND_RANGE_KEYS.includes(v) || (v.startsWith('saved:') && v.length > 6 && v.length <= 46));
export const trendRangeOptions = (saved = []) => [...TREND_RANGES.map(({ key, label }) => ({ key, label })), ...saved.map((r) => ({ key: `saved:${r.name}`, label: r.name }))];

// The points of a { x: [YYYY-MM-DD], y: [value] } trend that fall in the chosen range, ending today. The input is left alone.
export function limitTrend(trend, rangeKey, today, saved = []) {
  const built = TREND_RANGES.find((r) => r.key === rangeKey);
  const mine = typeof rangeKey === 'string' && rangeKey.startsWith('saved:') ? saved.find((r) => `saved:${r.name}` === rangeKey) : null;
  const last = built ? built.last : mine ? { amount: mine.amount, unit: mine.unit } : null;   // an unknown (or deleted) range shows everything
  const x = trend?.x ?? [], y = trend?.y ?? [];
  if (!last) return { x: [...x], y: [...y] };
  const from = durationStart(last, today);
  const keep = x.map((label, i) => (label >= from && label <= today ? i : -1)).filter((i) => i >= 0);
  return { x: keep.map((i) => x[i]), y: keep.map((i) => y[i]) };
}

const MAX_DAILY_DAYS = 62;   // a period longer than this is shown by week

// Monday of the week a YYYY-MM-DD date falls in.
export function weekStart(day) {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.toISOString().slice(0, 10);
}

// Tickets created per day (a period of up to 62 days) or per week starting Monday, from the first day to the last, with the empty days and
// weeks included so gaps are visible. A ticket counts on the date it was created in UTC, which is how the period itself is applied.
export function ticketTrend(rows, from, to) {
  const days = daysBetween(from, to) + 1;
  const weekly = days > MAX_DAILY_DAYS;
  const bucketOf = (day) => (weekly ? weekStart(day) : day);
  const labels = [];
  for (let day = bucketOf(from); day <= to; day = addDays(day, weekly ? 7 : 1)) labels.push(day);
  const counts = new Map(labels.map((l) => [l, 0]));
  for (const row of rows) {
    const created = String(row.created_at ?? '').slice(0, 10);
    if (created < from || created > to) continue;
    const bucket = bucketOf(created);
    if (counts.has(bucket)) counts.set(bucket, counts.get(bucket) + 1);
  }
  return { heading: weekly ? 'Tickets Created Per Week (Weeks Start On Monday)' : 'Tickets Created Per Day', type: 'bar', x: labels, y: labels.map((l) => counts.get(l)), weekly };
}
