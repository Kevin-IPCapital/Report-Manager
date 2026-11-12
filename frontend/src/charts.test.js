import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

// Every graph can be minimised. The charts that draw themselves (bars, donut) are wrapped in the collapsible ChartCard inside their own
// component; the trend line is drawn by pages and components, so any file that draws one must also use a ChartCard.
const root = fileURLToPath(new URL('.', import.meta.url));   // a real path on Windows too (spaces, drive letters)
const walk = (dir) => readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]));
const sources = walk(root).filter((f) => f.endsWith('.jsx')).map((f) => [f.slice(root.length).replaceAll('\\', '/'), readFileSync(f, 'utf8')]);   // names with forward slashes everywhere

test('a trend line is only ever drawn inside a ChartCard, so it can be minimised', () => {
  const offenders = sources.filter(([name, text]) => name !== 'components/TrendChart.jsx' && text.includes('<TrendChart') && !text.includes('<ChartCard')).map(([name]) => name);
  assert.deepEqual(offenders, []);
});

test('the bar and donut charts are wrapped in a ChartCard themselves', () => {
  for (const name of ['components/BarChart.jsx', 'components/Donut.jsx']) {
    assert.ok(sources.find(([n]) => n === name)?.[1].includes('<ChartCard'), name);
  }
});

test('no page draws a chart element directly, outside the chart components', () => {
  const direct = sources.filter(([name, text]) => !name.startsWith('components/') && /<(svg|canvas)\b/.test(text) && /chart/i.test(text)).map(([name]) => name);
  assert.deepEqual(direct, []);
});

test('the trend charts the app has are the ones this test knows about', () => {
  const users = sources.filter(([name, text]) => name !== 'components/TrendChart.jsx' && text.includes('<TrendChart')).map(([name]) => name).sort();
  assert.deepEqual(users, ['components/TicketExplorer.jsx', 'pages/Patches.jsx']);   // a new one is a prompt to check it is minimisable
});
