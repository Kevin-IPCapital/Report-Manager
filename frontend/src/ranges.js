// Date ranges you name and save: a duration such as "last 3 months" under a name of your own. Only the length is kept; the dates are worked out
// each time one is used, so "Board pack" always means the three months up to today. Kept in the browser, for each person. Pure, so it can be tested.
import { durationRange, describeDuration } from './report/dates.js';
import { DURATION_UNITS } from './report/config.js';

export const MAX_SAVED_RANGES = 20;
export const MAX_RANGE_NAME = 40;

const plain = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const nameKey = (name) => String(name).trim().toLowerCase();

// A length: whole days, weeks or months, within what the report rules allow for that unit.
export const isDuration = (v) => plain(v) && Object.hasOwn(DURATION_UNITS, v.unit) && Number.isInteger(v.amount) && v.amount >= 1 && v.amount <= DURATION_UNITS[v.unit];
export const isSavedRange = (v) => plain(v) && Object.keys(v).sort().join() === 'amount,name,unit' && typeof v.name === 'string'
  && v.name.trim() === v.name && v.name.length >= 1 && v.name.length <= MAX_RANGE_NAME && isDuration(v);
// What may be read back from storage: a short list of valid ranges with different names (ignoring case).
export const isSavedRanges = (v) => Array.isArray(v) && v.length <= MAX_SAVED_RANGES && v.every(isSavedRange) && new Set(v.map((r) => nameKey(r.name))).size === v.length;

const fail = (error) => ({ ok: false, error });

// Add a range to a list: { ok: true, list } or { ok: false, error } with a sentence to show. The list given is not changed.
export function addRange(list, { name, amount, unit }) {
  const clean = String(name ?? '').trim();
  const range = { name: clean, amount: Number(amount), unit };
  if (!clean) return fail('Give the range a name.');
  if (clean.length > MAX_RANGE_NAME) return fail(`A name can have at most ${MAX_RANGE_NAME} characters.`);
  if (!Object.hasOwn(DURATION_UNITS, unit)) return fail('Choose days, weeks or months.');
  if (!isDuration(range)) return fail(`A range is from 1 to ${DURATION_UNITS[unit]} ${unit}.`);
  if (list.some((r) => nameKey(r.name) === nameKey(clean))) return fail('You already have a range with that name.');
  if (list.length >= MAX_SAVED_RANGES) return fail(`At most ${MAX_SAVED_RANGES} ranges can be saved.`);
  return { ok: true, list: [...list, range] };
}

export const removeRange = (list, name) => list.filter((r) => nameKey(r.name) !== nameKey(name));

// The quick buttons: the same shape as the built-in presets ({ key, label, from, to }), with the dates worked out for today.
export const savedPresets = (list, today = new Date()) => list.map((r) => ({ key: `saved:${r.name}`, label: r.name, title: describeDuration(r), ...durationRange(r, today) }));
