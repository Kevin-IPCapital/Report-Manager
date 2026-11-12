import { api } from '../api/adapter';
import { asOfView } from '../freshness';
import { define } from '../glossary';
import { useApi } from './useApi';

// "Patch data as of 9 Nov 2026 12:29 (2 days ago). Stale: the last 3 collections failed": under a page's heading, so figures that have gone
// stale never look current. `source` is `patches`, `servers`, `backups` or `tickets`. If the answer cannot be had the line is simply absent;
// it never blocks the page.
export function DataAsOf({ source }) {
  const { data } = useApi(() => api.getFreshness(), []);
  const entry = data?.sources?.find((s) => s.key === source);
  if (!entry) return null;
  const view = asOfView(entry);
  // the hover text is what the term means, then where this particular time comes from
  return <p class={`data-as-of ${view.tone}`} title={`${define('Data As Of') ?? ''} ${view.title}`.trim()} role="status">{view.text}</p>;
}
