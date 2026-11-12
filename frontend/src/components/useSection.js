import { api } from '../api/adapter';
import { useDateRange } from '../state/DateRangeContext';
import { useApi } from './useApi';

// Fetches a section for the APPLIED date range. It refetches only when the range is applied
// or "Refresh data" is pressed, never because a filter or tab changed.
export function useSection(section) {
  const { applied, refreshKey } = useDateRange();
  return useApi(() => api.getSection(section, { from: applied.from, to: applied.to }), [section, applied.from, applied.to, refreshKey]);
}
