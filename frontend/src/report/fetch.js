// Gets the data a report needs, once per distinct request: ten ticket blocks for different customers cost one ticket request.
// `getSection(name, params)` is the API adapter's function, passed in so this can be tested without a network.
import { filterValues } from './config.js';

const scopeParams = (scope) => {
  const m = /^(client|org):(\d+)$/.exec(scope || '');
  return m ? { [m[1]]: m[2] } : {};
};

export async function collect(config, dates, getSection) {
  const cache = new Map();
  const once = (key, load) => {
    if (!cache.has(key)) cache.set(key, load());
    return cache.get(key);
  };
  const weights = Object.keys(config.weights ?? {}).length ? { weights: JSON.stringify(config.weights) } : {};   // the report's own weights, if it has any
  const patchesFor = (block) => once(`patches|${block.filters.patchScope}`, () => getSection('patches', { ...scopeParams(block.filters.patchScope), ...weights }));
  const data = {};
  for (const block of config.blocks) {
    if (block.type === 'tickets') data[block.id] = await once(`tickets|${dates.from}|${dates.to}`, () => getSection('tickets', { from: dates.from, to: dates.to }));
    else if (block.type === 'server-health') data[block.id] = await once('server-health', () => getSection('server-health'));
    else if (block.type === 'backups') data[block.id] = await once('backups', () => getSection('backups'));
    else if (block.type === 'patches') data[block.id] = await patchesFor(block);
    else if (block.type === 'patch-details') {
      const patches = await patchesFor(block);
      const details = [];
      const wanted = filterValues(block.filters.patchType);
      for (const t of (patches.types ?? []).filter((x) => !wanted.length || wanted.includes(x.key))) {
        for (const c of t.categories ?? []) {
          const response = await once(`entries|${block.filters.patchScope}|${t.key}|${c.category_id}`,
            () => getSection('patch-entries', { type: t.key, category: c.category_id, ...scopeParams(block.filters.patchScope) }));
          details.push(...(response.entries?.rows ?? []).map((r) => ({ ...r, patch_type: t.label })));
        }
      }
      data[block.id] = { patches, details };
    }
  }
  return data;
}
