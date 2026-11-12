// Date choices for running a report. Local dates, as YYYY-MM-DD.
import { durationStart } from '../dateMath.js';

const pad = (n) => String(n).padStart(2, '0');
export const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);

export function presets(today = new Date()) {
  const y = today.getFullYear(), m = today.getMonth(), q = Math.floor(m / 3);
  const range = (key, label, from, to) => ({ key, label, from: iso(from), to: iso(to) });
  return [
    range('last-30', 'Last 30 days', addDays(today, -29), today),
    range('this-month', 'This month', new Date(y, m, 1), today),
    range('last-month', 'Last month', new Date(y, m - 1, 1), new Date(y, m, 0)),
    range('this-quarter', 'This quarter', new Date(y, q * 3, 1), today),
    range('last-quarter', 'Last quarter', new Date(y, q * 3 - 3, 1), new Date(y, q * 3, 0)),
  ];
}

// The dates for "the last N days / weeks / months", ending today, counting today: the last 30 days is today and the 29 before it; the last
// 3 months starts the day after the same date 3 months back (on 10 Nov it runs from 11 Aug).
export function durationRange(last, today = new Date()) {
  const to = iso(today);
  return { from: durationStart(last, to), to };
}
export const describeDuration = ({ amount, unit }) => `Last ${amount} ${amount === 1 ? unit.slice(0, -1) : unit}`;

// The dates a run uses: the ones entered, else the report's own (dates, or a duration worked out from today), else the last 30 days.
// Throws if they cannot be used.
export function resolveDates(chosen, saved, today = new Date()) {
  if (!chosen?.from && !chosen?.to && !saved?.from && !saved?.to && saved?.last) return durationRange(saved.last, today);   // a duration: worked out from today
  const fallback = presets(today)[0];
  const from = chosen?.from || saved?.from || fallback.from;
  const to = chosen?.to || saved?.to || (chosen?.from || saved?.from ? iso(today) : fallback.to);
  for (const value of [from, to]) if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error('Dates must use YYYY-MM-DD.');
  if (from > to) throw new Error('Start date must not be after end date.');
  return { from, to };
}
