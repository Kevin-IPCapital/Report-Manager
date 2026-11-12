// Remembering a date range from one visit to the next. A range that ends today is kept as its LENGTH ("the last 30 days"), so the next
// visit gets the last 30 days up to that day's today and not the same fixed dates, which would be stale. A range that ends on another
// day (a past month, say) is kept as its dates.
import { addDays, daysBetween, isIsoDay } from './dateMath.js';

const MAX_DAYS = 3660;

export function encodeRange(range, today) {
  if (!range || !isIsoDay(range.from) || !isIsoDay(range.to) || range.from > range.to) return null;
  return range.to === today ? { days: daysBetween(range.from, range.to) } : { from: range.from, to: range.to };
}

// Is this something encodeRange could have written? (Anything else in storage is ignored.)
export function isRange(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  if ('days' in value) return Number.isInteger(value.days) && value.days >= 0 && value.days <= MAX_DAYS && Object.keys(value).length === 1;
  return isIsoDay(value.from) && isIsoDay(value.to) && value.from <= value.to && Object.keys(value).length === 2;
}

// The {from, to} to use today for something remembered, or null when there is nothing usable.
export function decodeRange(saved, today) {
  if (!isRange(saved)) return null;
  return 'days' in saved ? { from: addDays(today, -saved.days), to: today } : { from: saved.from, to: saved.to };
}
