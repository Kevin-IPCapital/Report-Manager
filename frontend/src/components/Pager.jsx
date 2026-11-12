import { PAGE_SIZES } from '../pagination';

// Paging controls shared by the tables and the card lists. `view` is what paginate() returned. Hidden while everything fits on the
// smallest page.
export function Pager({ view, size, onSize, onPage, sizes = PAGE_SIZES, unit = 'Rows' }) {
  if (view.total <= sizes[0]) return null;
  return (
    <div class="pager">
      <label>{unit} per page
        <select value={size} onChange={(e) => onSize(Number(e.target.value))}>
          {sizes.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
      <span class="count">{view.from}–{view.to} of {view.total}</span>
      <div class="pager-buttons">
        <button class="btn small" disabled={view.page === 0} onClick={() => onPage(view.page - 1)}>‹ Previous</button>
        <span>Page {view.page + 1} of {view.pages}</span>
        <button class="btn small" disabled={view.page >= view.pages - 1} onClick={() => onPage(view.page + 1)}>Next ›</button>
      </div>
    </div>
  );
}
