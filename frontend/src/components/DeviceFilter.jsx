// A filter box for device names: type part of a name (or several words) and the page shows the matching devices. Suggestions come
// from the names on the page. Shared by every page that has devices; the matching itself is matchesDevice() in filter.js.
const MAX_SUGGESTIONS = 500;

export function DeviceFilter({ value, onChange, names }) {
  const unique = [...new Set((names ?? []).filter(Boolean))].sort((a, b) => a.localeCompare(b)).slice(0, MAX_SUGGESTIONS);
  return (
    <label class="field">Device
      <input type="search" list="device-names" value={value} placeholder="Part of a device name" aria-label="Filter by device name" onInput={(e) => onChange(e.currentTarget.value)} />
      <datalist id="device-names">{unique.map((n) => <option key={n} value={n} />)}</datalist>
    </label>
  );
}
