import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { validateTemplate } from './config.js';
import { extractReport } from './extract.js';
import { collect } from './fetch.js';
import { buildPdf } from './pdf.js';

// The API's real response shapes (produced by the backend code itself: docs/tools/generate_fixtures.py).
const api = JSON.parse(readFileSync(new URL('./fixtures/api-responses.json', import.meta.url), 'utf8'));
const dates = { from: '2026-10-01', to: '2026-10-31' };

function serve() {
  const calls = [];
  const getSection = async (name, params = {}) => { calls.push([name, params]); return structuredClone(api[name]); };
  return { calls, getSection };
}
const run = async (template) => {
  const config = validateTemplate(template);
  const { calls, getSection } = serve();
  const data = await collect(config, dates, getSection);
  return { config, calls, ...extractReport(config, data, dates) };
};
const pdfPages = async (report, config) => {
  const blob = buildPdf({ title: config.title, subtitle: 'Live source data', period: dates, sections: report.sections, annex: report.annex, generatedAt: new Date('2026-11-10T08:00:00Z') });
  const text = Buffer.from(await blob.arrayBuffer()).toString('latin1');
  return { text, pages: [...text.matchAll(/stream\n([\s\S]*?)\nendstream/g)].map((m) => m[1]) };
};

test('every section kind reads the real API shapes and produces content', async () => {
  const { sections, warnings } = await run({ version: 2, blocks: [{ type: 'tickets' }, { type: 'patches' }, { type: 'patch-details' }, { type: 'server-health' }, { type: 'backups' }] });
  assert.deepEqual(warnings, []);
  assert.deepEqual(sections.map((s) => s.title), ['Client Tickets', 'Patch Compliance', 'Patch Details', 'Endpoint Health', 'Backup Health']);
  for (const section of sections) {
    assert.ok(section.items.length > 0, section.title);
    const kpis = section.items.find((i) => i.kpis);
    if (kpis) assert.ok(kpis.kpis.every((k) => k.value !== undefined && k.value !== null), `${section.title}: every tile has a value`);
  }
});

test('the numbers in the report are the numbers in the API response', async () => {
  const { sections } = await run({ version: 2, blocks: [{ type: 'tickets' }, { type: 'backups' }, { type: 'patches' }] });
  const tickets = sections[0].items[0].kpis.find((k) => k.label === 'Ticket Count').value;
  assert.equal(tickets, api.tickets.tickets.rows.length);
  assert.equal(sections[1].items[0].kpis.find((k) => k.label === 'Devices').value, api.backups.rows.length);
  const entries = api.patches.types.flatMap((t) => t.categories);
  const installed = entries.reduce((s, c) => s + c.installed, 0), missing = entries.reduce((s, c) => s + c.missing, 0);
  assert.equal(sections[2].items[0].kpis.find((k) => k.label === 'Patch entries').value, installed + missing);
  assert.equal(sections[2].items[0].kpis.find((k) => k.label === 'Overall score').value, `${api.patches.overall.current}%`);
});

test('a filter on a real customer name keeps only that customer, whatever the case', async () => {
  const name = api.tickets.tickets.rows[0].company_name;
  const { sections } = await run({ version: 2, blocks: [{ type: 'tickets', filters: { customer: name.toUpperCase() } }] });
  const expected = api.tickets.tickets.rows.filter((r) => r.company_name === name).length;
  assert.equal(sections[0].items[0].kpis[0].value, expected);
});

test('a client scope is sent as a parameter, and patch details ask for every category', async () => {
  const option = api.patches.clients[0];
  const { calls } = await run({ version: 2, blocks: [{ type: 'patch-details', filters: { patchScope: option.key } }] });
  const [kind, id] = option.key.split(':');
  assert.deepEqual(calls[0], ['patches', { [kind]: id }]);
  const categories = api.patches.types.reduce((n, t) => n + t.categories.length, 0);
  assert.equal(calls.filter((c) => c[0] === 'patch-entries').length, categories);
});

test('a whole report becomes a PDF whose contents page is right, with and without the annex', async () => {
  const blocks = [{ type: 'tickets' }, { type: 'patches' }, { type: 'server-health' }, { type: 'backups' }];
  for (const annex of [false, true]) {
    const report = await run({ version: 2, annex, blocks });
    const { text, pages } = await pdfPages(report, report.config);
    assert.equal(report.annex.length, annex ? 4 : 0);
    assert.equal(/\/Count (\d+)/.exec(text)[1], String(pages.length));
    const strings = (page) => [...page.matchAll(/\(((?:\\.|[^\\)])*)\) Tj/g)].map((m) => m[1].replace(/\\([()\\])/g, '$1'));
    const toc = strings(pages[1]);
    const entries = [];
    toc.forEach((s, i) => { if (/^(\d+\. |Annex )/.test(s) && /^\d+$/.test(toc[i + 1] ?? '')) entries.push([s, Number(toc[i + 1])]); });
    assert.equal(entries.length, 4 + report.annex.length);
    for (const [label, page] of entries) assert.ok(strings(pages[page - 1]).includes(label), `${label} is on page ${page} (annex: ${annex})`);
    assert.equal(strings(pages.at(-1)).some((s) => s.startsWith('Page ')), true);
  }
});

test('a report that carries its own weights asks the server for scores with them', async () => {
  const { calls } = await run({ version: 2, weights: { 'windows-security': 3 }, blocks: [{ type: 'patches' }] });
  assert.deepEqual(calls[0], ['patches', { weights: '{"windows-security":3}' }]);
});
