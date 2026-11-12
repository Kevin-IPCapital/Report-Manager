import { useDateRange } from '../state/DateRangeContext';

const dateFmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });
const fmt = (iso) => { const [y, m, d] = iso.split('-').map(Number); return dateFmt.format(new Date(y, m - 1, d)); };

// The period the data was fetched for (the APPLIED range, which can differ from the draft being edited).
export function Period() {
  const { applied } = useDateRange();
  return <p class="subtitle">Showing {fmt(applied.from)} – {fmt(applied.to)}</p>;
}
