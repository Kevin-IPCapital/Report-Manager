// Column search. Text columns match "contains" (case-insensitive, on what the cell shows). Number, percentage, duration and date
// columns also understand comparisons:
//   >5   >=5   <5   <=5   =5   !=5        a bare number means "equals"
//   5..10                                  an inclusive range
//   >2, <10                                comma means "and"
//   duration:  >2h  <=30m  1d..2d          units d, h, m (a bare number is hours)
//   percent:   <90  80%..100%
//   size:      >100gb  <=1tb  500mb..2gb  (units kb, mb, gb, tb; a bare number is GB, as the cells show)
//   date:      >2026-10-01  2026-10-01..2026-10-31   (a whole day: "=2026-10-05" is that day; "<=" runs to the end of it)
// If the text is not a valid comparison for the column it falls back to a plain text search.

const NUMERIC_TYPES = new Set(['percent', 'duration', 'number', 'bytes']);

// 'number', 'date' or 'text'. A column with no declared type counts as numeric when every value present is a number.
export function columnKind(column, rows) {
  if (column.type === 'datetime') return 'date';
  if (NUMERIC_TYPES.has(column.type)) return 'number';
  if (column.type && column.type !== 'text') return 'text'; // status, bool, flag, priority, link
  const values = rows.map((r) => r[column.key]).filter((v) => v != null && v !== '');
  return values.length > 0 && values.every((v) => typeof v === 'number') ? 'number' : 'text';
}

export function searchHint(kind, column, label) {
  if (kind === 'date') return `Search ${label} (e.g. >2026-10-01, 2026-10-01..2026-10-31)`;
  if (kind !== 'number') return `Search ${label}`;
  if (column.type === 'duration') return `Search ${label} (e.g. >2h, <=30m, 1h..4h)`;
  if (column.type === 'percent') return `Search ${label} (e.g. <90, 80..100)`;
  if (column.type === 'bytes') return `Search ${label} (e.g. >100gb, <=1tb)`;
  return `Search ${label} (e.g. >5, <=10, 5..20)`;
}

const OPERATORS = ['>=', '<=', '!=', '>', '<', '='];
const BYTE_UNITS = { b: 1, k: 1024, kb: 1024, m: 1024 ** 2, mb: 1024 ** 2, g: 1024 ** 3, gb: 1024 ** 3, t: 1024 ** 4, tb: 1024 ** 4 };
const DURATION_HOURS = { d: 24, day: 24, days: 24, h: 1, hr: 1, hrs: 1, hour: 1, hours: 1, m: 1 / 60, min: 1 / 60, mins: 1 / 60, minute: 1 / 60, minutes: 1 / 60 };

function parseNumber(token, column) {
  const text = token.trim();
  if (column.type === 'duration') {
    const m = /^(-?\d+(?:\.\d+)?)\s*([a-z]*)$/i.exec(text);
    if (!m) return null;
    const unit = m[2].toLowerCase();
    return unit === '' ? Number(m[1]) : unit in DURATION_HOURS ? Number(m[1]) * DURATION_HOURS[unit] : null;
  }
  if (column.type === 'bytes') {
    const b = /^(-?\d+(?:\.\d+)?)\s*([a-z]*)$/i.exec(text);
    if (!b) return null;
    const unit = b[2].toLowerCase();
    return unit === '' ? Number(b[1]) * BYTE_UNITS.gb : unit in BYTE_UNITS ? Number(b[1]) * BYTE_UNITS[unit] : null;
  }
  const m = /^(-?\d+(?:\.\d+)?)\s*%?$/.exec(text);
  return m ? Number(m[1]) : null;
}

// A whole local day as [start, end), in milliseconds.
function parseDay(token) {
  const m = /^(\d{4})[-/](\d{2})[-/](\d{2})$/.exec(token.trim());
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]) - 1, Number(m[3])];
  const start = new Date(y, mo, d);
  return start.getFullYear() === y && start.getMonth() === mo && start.getDate() === d ? { start: start.getTime(), end: new Date(y, mo, d + 1).getTime() } : null;
}

const tolerance = (column) => (column.type === 'duration' ? 0.5 / 60 : column.type === 'percent' ? 0.005 : column.type === 'bytes' ? 0.005 * 1024 ** 3 : 1e-9); // the cell rounds to a minute / 2 places

function numberCondition(op, operand, column) {
  const tol = tolerance(column);
  return {
    '>': (v) => v > operand + tol, '>=': (v) => v >= operand - tol, '<': (v) => v < operand - tol, '<=': (v) => v <= operand + tol,
    '=': (v) => Math.abs(v - operand) <= tol, '!=': (v) => Math.abs(v - operand) > tol,
  }[op];
}

function dayCondition(op, day) {
  return {
    '>': (v) => v >= day.end, '>=': (v) => v >= day.start, '<': (v) => v < day.start, '<=': (v) => v < day.end,
    '=': (v) => v >= day.start && v < day.end, '!=': (v) => v < day.start || v >= day.end,
  }[op];
}

// A function (raw value -> boolean) for a comparison query, or null if the query is not one.
export function comparison(query, column, kind) {
  if (kind === 'text') return null;
  const text = query.replace(/≥/g, '>=').replace(/≤/g, '<=').replace(/≠/g, '!=').replace(/<>/g, '!=').replace(/==/g, '=');
  const conditions = [];
  for (const part of text.split(',').map((p) => p.trim()).filter(Boolean)) {
    const range = part.split('..');
    if (range.length === 2) {
      if (kind === 'date') {
        const [lo, hi] = [parseDay(range[0]), parseDay(range[1])];
        if (!lo || !hi) return null;
        conditions.push((v) => v >= lo.start && v < hi.end);
      } else {
        const [lo, hi] = [parseNumber(range[0], column), parseNumber(range[1], column)];
        if (lo == null || hi == null) return null;
        const tol = tolerance(column);
        conditions.push((v) => v >= Math.min(lo, hi) - tol && v <= Math.max(lo, hi) + tol);
      }
      continue;
    }
    const op = OPERATORS.find((o) => part.startsWith(o)) ?? '=';
    const operand = part.slice(op === '=' && !part.startsWith('=') ? 0 : op.length).trim();
    if (kind === 'date') {
      const day = parseDay(operand);
      if (!day) return null;
      conditions.push(dayCondition(op, day));
    } else {
      const n = parseNumber(operand, column);
      if (n == null) return null;
      conditions.push(numberCondition(op, n, column));
    }
  }
  if (!conditions.length) return null;
  return (value) => {
    if (value == null || value === '') return false;
    const v = kind === 'date' ? new Date(value).getTime() : Number(value);
    return !Number.isNaN(v) && conditions.every((c) => c(v));
  };
}

// Does a row match a column's search? `display` is the text the cell shows.
export function matchesQuery(column, kind, rawValue, display, query) {
  const compare = comparison(query, column, kind);
  return compare ? compare(rawValue) : display.toLowerCase().includes(query.toLowerCase());
}

// Does a device match what was typed in a device filter? Every word typed must appear in one of the names (case-insensitive), so
// "srv file" finds "SRV-FILE-01". Nothing typed matches everything.
export function matchesDevice(query, ...names) {
  const words = String(query ?? '').toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const haystack = names.filter(Boolean).join(' ').toLowerCase();
  return words.every((w) => haystack.includes(w));
}
