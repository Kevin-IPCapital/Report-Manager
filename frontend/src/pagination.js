export const PAGE_SIZES = [10, 20, 50, 100];
export const DEFAULT_PAGE_SIZE = 20;

// One page of `items`. `page` is 0-based and is clamped into range, so a list that shrinks (a filter, a search) never
// leaves the table on a page that no longer exists. `from` and `to` are 1-based, for "21-40 of 63".
export function paginate(items, page, size) {
  const total = items.length;
  const pages = Math.max(1, Math.ceil(total / size));
  const current = Math.min(Math.max(0, page), pages - 1);
  const start = current * size;
  return { items: items.slice(start, start + size), page: current, pages, total, from: total ? start + 1 : 0, to: Math.min(total, start + size) };
}
