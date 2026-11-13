import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

// Layout problems that only show in a browser, kept from coming back. These read the stylesheet as text: they cannot prove how a page looks, but
// they stop the specific causes found when the pages were first looked at in a real browser (12 Nov 2026) from being removed by accident.
const css = readFileSync(new URL('./styles.css', import.meta.url), 'utf8');
const rule = (selector) => {
  const start = css.indexOf(selector);
  if (start < 0) return '';
  return css.slice(css.indexOf('{', start) + 1, css.indexOf('}', start));
};

test('the app\'s tables keep each cell on one line, so a table inside a card must say it may wrap', () => {
  assert.match(rule('th, td {'), /white-space:\s*nowrap/);                                        // the cause: every table cell is one line by default
  const card = rule('.patch-table th, .collected-table th, .patch-table td, .collected-table td');
  assert.match(card, /white-space:\s*normal/);                                                    // the cure: the card's tables wrap at spaces
  assert.match(card, /overflow-wrap:\s*break-word/);
  assert.doesNotMatch(card, /overflow-wrap:\s*anywhere/);                                         // "anywhere" split words in the middle ("collecte" / "d")
});

test('a card is wide enough for its tables', () => {
  const min = Number(/repeat\(auto-fill,\s*minmax\(min\(([\d.]+)rem/.exec(rule('.agent-grid {'))?.[1]);
  assert.ok(min >= 21.25, `cards are at least 21.25rem wide, not ${min}rem`);
});

test('the Up To Date tick is drawn, because a disabled native checkbox is too faint to tell ticked from empty', () => {
  assert.match(rule('.patch-tick {'), /border:\s*\.125rem solid/);
  assert.match(rule('.patch-tick.on {'), /background:\s*var\(--good\)/);
  assert.match(css, /\.patch-tick\.on::after/);
  assert.match(rule('.patch-tick.on::after'), /var\(--surface\)/);                               // the check mark uses the page colour, so it shows in both themes
});

test('the working tables line up with one another, with the numbers on the right and headings that wrap', () => {
  assert.match(rule('.working {'), /table-layout:\s*fixed/);
  assert.match(rule('.working th.num, .working td.num'), /text-align:\s*right/);
  assert.match(rule('.working th {'), /white-space:\s*normal/);
});

test('every card section has a rule that shows it opens and closes', () => {
  assert.match(css, /\.card-section\[open\] > summary::before/);
  assert.match(rule('.card-section > summary {'), /cursor:\s*pointer/);
});

test('the "data as of" line keeps its bullet beside the text, so a wrapped line hangs under the text, and is centred inside the centred Overall Score card', () => {
  assert.match(rule('.data-as-of {'), /display:\s*flex/);
  assert.match(rule('.data-as-of::before'), /flex:\s*none/);
  assert.match(rule('.overall .data-as-of {'), /display:\s*block/);                 // inside the card the bullet runs in with the text
  assert.match(rule('.overall .data-as-of {'), /text-align:\s*center/);
});

test('the "data as of" line stays inside the centred page column: it sets only its vertical margins', () => {
  assert.match(rule('main > * {'), /margin-inline:\s*auto/);                                  // every child of the page is centred in a column...
  const line = rule('.data-as-of {');
  assert.match(line, /margin-block:/);
  assert.doesNotMatch(line, /(^|[;\s])margin:/);                                              // ...and a margin shorthand with 0 at the sides would pull this line out of it, to the left, on a wide window
});

// ---- relative sizes only (the owner's rule, 12 Nov 2026): rem, em, %, fr, vw, vh, ch and clamp(), never px, so the page follows the reader's font size,
// zoom and window. These stop a pixel size from creeping back in.
const here = fileURLToPath(new URL('.', import.meta.url));
const walk = (dir) => readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? (e.name === 'fixtures' ? [] : walk(`${dir}${e.name}/`)) : [`${dir}${e.name}`]));
const sources = walk(here).filter((f) => /\.(css|jsx?)$/.test(f) && !f.endsWith('.test.js'));
const PIXELS = /(?<![\w-])-?(?:\d*\.)?\d+px\b/g;

test('no stylesheet, component or script gives a size in pixels', () => {
  assert.ok(sources.length > 60);
  const found = sources.flatMap((f) => (readFileSync(f, 'utf8').match(PIXELS) ?? []).map((m) => `${f.slice(here.length)}: ${m}`));
  assert.deepEqual(found, []);
  assert.doesNotMatch(readFileSync(new URL('../index.html', import.meta.url), 'utf8'), PIXELS);
});

test('every breakpoint is in em or rem, and the Settings code asks the browser about the same one the stylesheet uses for the narrow layout', () => {
  const all = [...css.matchAll(/@media \((?:max|min)-width:\s*([\d.]+)(\w+)\)/g)].map((m) => [m[1], m[2]]);
  assert.ok(all.length >= 2);
  for (const [, unit] of all) assert.match(unit, /^r?em$/);
  const asked = /matchMedia\('\(max-width:\s*([\d.]+)em\)'\)/.exec(readFileSync(new URL('./state/SettingsContext.jsx', import.meta.url), 'utf8'));
  assert.ok(asked, 'SettingsContext asks about a max-width in em');
  assert.ok(css.includes(`@media (max-width: ${asked[1]}em)`), `the stylesheet has a narrow layout at ${asked[1]}em`);
});

test('the sidebar is a share of the window between a smallest and a largest width', () => {
  assert.match(rule('.shell {'), /grid-template-columns:\s*clamp\([\d.]+rem,\s*[\d.]+vw,\s*[\d.]+rem\)\s+1fr/);
});

// ---- the 12-column grid
const grid = readFileSync(new URL('./grid.css', import.meta.url), 'utf8');
const BREAKPOINTS = { sm: 36, md: 48, lg: 64, xl: 80 };

test('the grid has col-N for every count from 1 to 12, and col-sm, col-md, col-lg and col-xl from the right width up', () => {
  for (let n = 1; n <= 12; n += 1) assert.match(grid, new RegExp(`^\\.col-${n} \\{ grid-column: span ${n}; \\}`, 'm'), `col-${n}`);
  for (const [name, em] of Object.entries(BREAKPOINTS)) {
    const block = new RegExp(`@media \\(min-width: ${em}em\\) \\{([^@]*?)\\n\\}`).exec(grid)?.[1] ?? '';
    for (let n = 1; n <= 12; n += 1) assert.match(block, new RegExp(`\\.col-${name}-${n} \\{ grid-column: span ${n}; \\}`), `col-${name}-${n}`);
  }
  const row = grid.slice(grid.indexOf('.row {') + 6, grid.indexOf('}', grid.indexOf('.row {')));
  assert.match(row, /grid-template-columns:\s*repeat\(12,\s*minmax\(0,\s*1fr\)\)/);
});

test('the grid is mobile first: wider rules come later, so they win, and every breakpoint is a min-width in em', () => {
  const order = [...grid.matchAll(/@media \(min-width: ([\d.]+)em\)/g)].map((m) => Number(m[1]));
  assert.deepEqual(order, Object.values(BREAKPOINTS));
  assert.doesNotMatch(grid, /max-width/);
  assert.ok(css.indexOf('.row > .chart:last-child') > -1);
});

test('the layouts that used fixed column counts now use the grid, and the old rules and class names are gone', () => {
  for (const old of ['.stats-4', '.charts', '.overview-top']) assert.ok(!css.includes(old), `${old} is gone from the stylesheet`);
  for (const f of sources.filter((x) => x.endsWith('.jsx'))) assert.doesNotMatch(readFileSync(f, 'utf8'), /class="(?:stats stats-4|charts|overview-top)"/, f.slice(here.length));
  const read = (name) => readFileSync(new URL(name, import.meta.url), 'utf8');
  assert.match(read('./components/ui.jsx'), /TILE_COLS = 'col-12 col-sm-6 col-lg-3'/);                   // four tiles across on a wide page, two on a medium one, one on a phone
  assert.match(read('./components/ChartCard.jsx'), /CHART_COLS = 'col-12 col-lg-6'/);                    // two charts side by side from 64em
  assert.match(read('./pages/Patches.jsx'), /overall col-12 col-lg-5 col-xl-4/);
  assert.match(read('./pages/Patches.jsx'), /clients-card col-12 col-lg-7 col-xl-8/);
});

test('a column class is used only inside a row: tiles in the auto-fit grid carry none', () => {
  const ui = readFileSync(new URL('./components/ui.jsx', import.meta.url), 'utf8');
  assert.match(ui, /cols = ''/);                                                                          // a tile has no column class unless its page puts it in a row
  assert.match(ui, /class="stats">\{data\.stats\.map\(\(s\) => <StatCard key=\{s\.label\} \{\.\.\.s\} \/>\)\}/);
});

test('a size that stands for a share of the screen is said as one', () => {
  assert.match(rule('.collection-log-text {'), /max-height:\s*60vh/);
  assert.match(rule('td.wrap {'), /max-width:\s*min\(28rem,\s*70vw\)/);
  assert.match(rule('.login form {'), /width:\s*min\(25rem,\s*92vw\)/);
});

test('a grid that fits as many columns as it can never asks for a column wider than the space it has', () => {
  const grids = [...css.matchAll(/repeat\(auto-(?:fill|fit),\s*(minmax\([^;}]*?\))\)/g)].map((m) => m[1]);
  assert.ok(grids.length >= 6);
  for (const g of grids) assert.match(g, /^minmax\(min\([\d.]+rem,\s*100%\),\s*1fr\)$/, `${g}: a minimum of N rem alone is wider than a small phone's page`);   // the cause of a card sticking out at 320px
});
