import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildPdf } from './pdf.js';

const NOW = new Date('2026-11-10T08:00:00Z');
const kpiSection = (title) => ({ title, notes: ['note'], items: [{ kpis: [{ label: 'Count', value: 3 }] }] });
const tableEntry = (title, rows = 3) => ({ title, notes: ['x'], items: [{ columns: ['A', 'B'], rows: Array.from({ length: rows }, (_, i) => [`a${i}`, `b${i}`]) }] });

// The file as latin1, so one character is one byte and the cross-reference offsets can be checked.
async function read(blob) {
  const text = Buffer.from(await blob.arrayBuffer()).toString('latin1');
  const pages = [...text.matchAll(/stream\n([\s\S]*?)\nendstream/g)].map((m) => m[1]);
  const strings = (page) => [...page.matchAll(/\(((?:\\.|[^\\)])*)\) Tj/g)].map((m) => m[1].replace(/\\([()\\])/g, '$1'));
  return { text, pages, strings: pages.map(strings) };
}
// Contents entries as [label, page] pairs.
const contents = ({ strings }) => {
  const entries = [];
  for (const page of strings.filter((strs) => strs.some((x) => x.startsWith('Contents')))) {
    for (let i = 0; i < page.length; i += 1) if (/^(\d+\. |Annex )/.test(page[i]) && /^\d+$/.test(page[i + 1] ?? '')) entries.push([page[i], Number(page[i + 1])]);
  }
  return entries;
};
const build = (args) => read(buildPdf({ title: 'Monthly pack', subtitle: 'Live source data', generatedAt: NOW, ...args }));

test('a cover, a contents page, then the body', async () => {
  const pdf = await build({ period: { from: '2026-10-01', to: '2026-10-31' }, sections: [kpiSection('Client Tickets')] });
  assert.equal(pdf.pages.length, 3);
  assert.ok(pdf.strings[0].includes('Monthly pack'));
  assert.ok(pdf.strings[0].includes('Ticket period: 2026-10-01 to 2026-10-31'));
  assert.ok(pdf.strings[1].includes('Contents'));
  assert.ok(pdf.strings[2].includes('1. Client Tickets'));
});

test('the cover has no period line when there is none, and no page number', async () => {
  const pdf = await build({ sections: [kpiSection('A')] });
  assert.equal(pdf.strings[0].some((s) => s.startsWith('Ticket period')), false);
  assert.equal(pdf.strings[0].some((s) => s.startsWith('Page ')), false);
});

test('every page after the cover says "Page n of N" with the right numbers', async () => {
  const pdf = await build({ sections: [kpiSection('A'), kpiSection('B')], annex: [tableEntry('Annex A: Detail', 80)] });
  const total = pdf.pages.length;
  assert.ok(total > 4, 'the annex table runs over several pages');
  pdf.strings.slice(1).forEach((page, i) => assert.ok(page.includes(`Page ${i + 2} of ${total}`), `page ${i + 2}`));
  assert.equal(/\/Count (\d+)/.exec(pdf.text)[1], String(total));
});

test('each contents entry points at the page where its heading really is', async () => {
  const pdf = await build({ sections: [kpiSection('Client Tickets'), kpiSection('Patch Compliance'), kpiSection('Backup Health')],
    annex: [tableEntry('Annex A: Ticket detail', 60), tableEntry('Annex B: Backup detail', 5)] });
  const entries = contents(pdf);
  assert.deepEqual(entries.map((e) => e[0]), ['1. Client Tickets', '2. Patch Compliance', '3. Backup Health', 'Annex A: Ticket detail', 'Annex B: Backup detail']);
  for (const [label, page] of entries) {
    assert.ok(pdf.strings[page - 1].includes(label), `${label} is on page ${page}`);
    const earlier = pdf.strings.slice(3, page - 1).some((p) => p.includes(label));
    assert.equal(earlier, false, `${label} does not appear on an earlier body page`);
  }
  assert.ok(entries[3][1] < entries[4][1]);
});

test('a long annex table pushes the next annex entry onto a later page, and the contents knows', async () => {
  const pdf = await build({ sections: [kpiSection('S')], annex: [tableEntry('Annex A: Long', 120), tableEntry('Annex B: Short', 2)] });
  const [a, b] = contents(pdf).slice(1);
  assert.ok(b[1] - a[1] >= 2, `Annex A runs over ${b[1] - a[1]} pages`);
});

test('each annex entry starts on a page of its own', async () => {
  const pdf = await build({ sections: [kpiSection('S')], annex: [tableEntry('Annex A: One'), tableEntry('Annex B: Two')] });
  const pages = contents(pdf).slice(1).map((e) => e[1]);
  assert.equal(new Set(pages).size, 2);
  assert.equal(pdf.strings[pages[0] - 1].includes('Annex B: Two'), false);
});

test('more than 30 entries need a second contents page, and the page numbers allow for it', async () => {
  const sections = Array.from({ length: 40 }, (_, i) => ({ title: `Section ${i + 1}`, notes: [], items: [{ paragraph: 'text' }] }));
  const pdf = await build({ sections });
  assert.ok(pdf.strings[1].includes('Contents'));
  assert.ok(pdf.strings[2].includes('Contents (continued)'));
  const entries = contents(pdf);
  assert.equal(entries.length, 40);
  assert.ok(entries[0][1] >= 4, 'the body starts after the cover and two contents pages');
  for (const [label, page] of entries) assert.ok(pdf.strings[page - 1].includes(label), label);
});

test('a report with nothing in it is still a valid document', async () => {
  const pdf = await build({ sections: [] });
  assert.ok(pdf.strings.some((page) => page.includes('No data was available for this report.')));
  assert.ok(pdf.text.startsWith('%PDF-1.4') && pdf.text.trimEnd().endsWith('%%EOF'));
});

test('the cross-reference table points at every object', async () => {
  const { text } = await build({ sections: [kpiSection('A')], annex: [tableEntry('Annex A: D', 4)] });
  const start = Number(/startxref\n(\d+)/.exec(text)[1]);
  assert.ok(text.slice(start).startsWith('xref'));
  const offsets = [...text.slice(start).matchAll(/(\d{10}) 00000 n/g)].map((m) => Number(m[1]));
  offsets.forEach((offset, i) => assert.ok(text.slice(offset).startsWith(`${i + 1} 0 obj`), `object ${i + 1}`));
});

test('text the PDF font cannot show becomes a question mark instead of breaking the file', async () => {
  const { text, strings } = await build({ title: 'Report \u2014 \u65e5\u672c', sections: [kpiSection('Caf\u00e9 \u2603')] });
  assert.ok(strings[0].some((s) => s.startsWith('Report')));
  assert.ok(strings[2].includes('1. Caf\u00e9 ?'));
  assert.ok(text.trimEnd().endsWith('%%EOF'));
});
