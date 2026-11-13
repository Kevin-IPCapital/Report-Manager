// "Worst first": the order each page starts in. What counts as worst is decided per page:
//   Tickets   late or flagged first (a late first response, or the SLA missed), then the longest open
//   Servers   infected, then not connected, then the oldest data
//   Patches   the category with the lowest compliance first
//   Backups   critical, then warning, then OK; within each, the longest since a successful backup; then the most red flags
// Every function returns a new array and leaves the one it was given alone. Ties keep a steady, readable order (names).
import { connectionOf } from './serverHealth.js';

const text = (v) => String(v ?? '');
const byText = (a, b) => text(a).localeCompare(text(b), undefined, { sensitivity: 'base', numeric: true });
const num = (v, whenMissing) => (v == null || v === '' || Number.isNaN(Number(v)) ? whenMissing : Number(v));
const time = (v) => { const t = Date.parse(v); return Number.isNaN(t) ? Infinity : t; };

// ---- tickets
export const isLate = (t) => Boolean(t.rowStatus) || t.sla_met === false;
export const isOpen = (t) => !t.completed_at;

export function compareTickets(a, b) {
  if (isLate(a) !== isLate(b)) return isLate(a) ? -1 : 1;
  if (isOpen(a) !== isOpen(b)) return isOpen(a) ? -1 : 1;
  // open: the oldest first (open longest); closed: the slowest to resolve first
  const diff = isOpen(a) ? time(a.created_at) - time(b.created_at) : num(b.resolution_hours, -1) - num(a.resolution_hours, -1);
  return diff || byText(a.ticket_number, b.ticket_number);
}
export const worstTickets = (rows) => [...rows].sort(compareTickets);

// ---- servers (SentinelOne endpoints)
export function serverRank(s) {
  if (s.infected === true) return 0;
  const connection = connectionOf(s.network_status);
  return connection === 'disconnected' ? 1 : connection === 'unknown' ? 2 : 3;
}
export const compareServersWorst = (a, b) => serverRank(a) - serverRank(b) || num(b.data_age_hours, -1) - num(a.data_age_hours, -1) || byText(a.customer_name, b.customer_name) || byText(a.computer_name, b.computer_name);

// The orders a person can choose between on Server Health.
export const SERVER_SORTS = [
  { key: 'worst', label: 'Worst first', compare: compareServersWorst },
  { key: 'name', label: 'Name A–Z', compare: (a, b) => byText(a.computer_name, b.computer_name) || byText(a.customer_name, b.customer_name) },
  { key: 'customer', label: 'Customer, then name', compare: (a, b) => byText(a.customer_name, b.customer_name) || byText(a.computer_name, b.computer_name) },
  { key: 'age', label: 'Oldest data first', compare: (a, b) => num(b.data_age_hours, -1) - num(a.data_age_hours, -1) || byText(a.computer_name, b.computer_name) },
];
export const SERVER_SORT_KEYS = SERVER_SORTS.map((s) => s.key);
export const sortServers = (rows, key = 'worst') => [...rows].sort((SERVER_SORTS.find((s) => s.key === key) ?? SERVER_SORTS[0]).compare);

// ---- patch categories: lowest compliance first; a category with no figure last
export const compareCategories = (a, b) => num(a.compliance, Infinity) - num(b.compliance, Infinity) || num(b.missing, 0) - num(a.missing, 0) || byText(a.category, b.category);
export const worstCategories = (rows) => [...rows].sort(compareCategories);

// ---- Cove backups
const HEALTH_RANK = { CRITICAL: 0, WARNING: 1, OK: 2 };
export const compareBackups = (a, b) => (HEALTH_RANK[a.health] ?? 3) - (HEALTH_RANK[b.health] ?? 3)
  || num(b.days_since_success, Infinity) - num(a.days_since_success, Infinity)   // never succeeded is the longest of all
  || num(b.red_flag_count, 0) - num(a.red_flag_count, 0)                           // a tie breaker (owner, 12 Nov 2026): the more red flags, the worse
  || byText(a.customer, b.customer) || byText(a.device_name, b.device_name);
export const worstBackups = (rows) => [...rows].sort(compareBackups);
