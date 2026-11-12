import { Fragment } from 'preact';
import { titleCase } from '../text';
import { DateRange } from './DateRange';

// Breadcrumb, title and (optionally) the date controls. `trail` lists the levels below the page,
// e.g. [{ label, onClick }, { label }]; every level except the last is a link back up, and the page
// name links to the top (`onTitle`). Pages without a date range pass range={false}.
export function PageHeader({ title, range = true, trail = [], onTitle }) {
  const last = trail[trail.length - 1];
  return (
    <>
      <nav class="crumbs" aria-label="Breadcrumb">
        Home <span aria-hidden="true">/</span>{' '}
        {trail.length
          ? <button class="rowlink" onClick={onTitle}>{titleCase(title)}</button>
          : <span aria-current="page">{titleCase(title)}</span>}
        {trail.map((t, i) => (
          <Fragment key={i}>
            {' '}<span aria-hidden="true">/</span>{' '}
            {i < trail.length - 1
              ? <button class="rowlink" onClick={t.onClick}>{titleCase(t.label)}</button>
              : <span aria-current="page">{titleCase(t.label)}</span>}
          </Fragment>
        ))}
      </nav>
      <h1>{titleCase(last?.label || title)}</h1>
      {range && <DateRange />}
    </>
  );
}
