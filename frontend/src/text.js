// Capitalises the first letter of every word ("ticket by client" -> "Ticket By Client").
// The rest of each word is left alone, so acronyms such as SLA and CPU survive.
// A product name that is written with a capital only at its start keeps that spelling: the hyphen in "N-central" would otherwise start a new word.
const KEEP = [['N-Central', 'N-central']];
export const titleCase = (s) => KEEP.reduce((text, [wrong, right]) => text.replaceAll(wrong, right), String(s ?? '').replace(/\p{L}[\p{L}\p{N}'’]*/gu, (w) => w[0].toUpperCase() + w.slice(1)));

// Hours -> "1 day, 3 hours, 5 min". Zero units are left out, and any leftover seconds count as one more minute.
export const formatDuration = (hours) => {
  if (hours == null) return '—';
  const minutes = Math.ceil(Math.round(hours * 3600) / 60);
  const days = Math.floor(minutes / 1440), hrs = Math.floor((minutes % 1440) / 60), mins = minutes % 60;
  const unit = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  return [days && unit(days, 'day', 'days'), hrs && unit(hrs, 'hour', 'hours'), mins && `${mins} min`].filter(Boolean).join(', ') || '0 min';
};

// Bytes -> "3.00 GB". Binary units (1 GB = 1024^3 bytes), the same as the Cove collector reports.
export const formatBytes = (bytes) => {
  if (bytes == null || bytes === '' || Number.isNaN(Number(bytes))) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let value = Number(bytes), i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return i === 0 ? `${value} B` : `${value.toFixed(2)} ${units[i]}`;
};
