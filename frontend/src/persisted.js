// The checks for each kind of remembered value. Something that fails its check is ignored and the page starts from its defaults.
import { PAGE_SIZES } from './pagination.js';

const plain = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

// Chosen filters: {name: text or true}, as the pages keep them.
export const isFilters = (v) => plain(v) && Object.keys(v).length <= 50
  && Object.entries(v).every(([k, x]) => k.length > 0 && k.length <= 60 && (x === true || (typeof x === 'string' && x.length > 0 && x.length <= 200)));

export const isText = (v) => typeof v === 'string' && v.length <= 200;

// A table's remembered sort, or null for "none".
export const isSort = (v) => v === null || (plain(v) && typeof v.key === 'string' && v.key.length > 0 && v.key.length <= 80 && (v.dir === 1 || v.dir === -1) && Object.keys(v).length === 2);

export const isPageSize = (sizes = PAGE_SIZES) => (v) => sizes.includes(v);

export const isOneOf = (options) => (v) => options.includes(v);

// Keep a remembered sort only if its column is still there.
export const usableSort = (sort, columns) => (sort && columns.some((c) => c.key === sort.key) ? sort : null);
