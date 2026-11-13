import { MultiFilter } from './MultiFilter';

const MAX_NAMES = 500;

// The client filter and the multi-device filter that sit above the Server Health and Backups pages. Each holds several values as removable bubbles;
// nothing chosen means everything is shown. Clients are chosen from the ones on the page; a device is chosen from the names on the page, and a chosen
// device matches by name the way a typed one always did (see matchesDevices in filter.js). The page keeps the two lists, so they are remembered.
export function ClientDeviceFilters({ clients, onClients, clientOptions, devices, onDevices, deviceNames }) {
  const names = [...new Set((deviceNames ?? []).filter(Boolean))].sort((a, b) => a.localeCompare(b)).slice(0, MAX_NAMES);
  return (
    <div class="toolbar filters">
      <MultiFilter label="Clients" max={50} emptyLabel="All clients" value={clients} options={clientOptions} onChange={(v) => onClients(Array.isArray(v) ? v : v ? [v] : [])} />
      <MultiFilter label="Devices" max={50} emptyLabel="All devices" value={devices} options={names.map((n) => ({ value: n, label: n }))} onChange={(v) => onDevices(Array.isArray(v) ? v : v ? [v] : [])} />
    </div>
  );
}
