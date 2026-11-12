import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

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
  const min = Number(/repeat\(auto-fill,\s*minmax\((\d+)px/.exec(rule('.agent-grid {'))?.[1]);
  assert.ok(min >= 340, `cards are at least 340px wide, not ${min}px`);
});

test('the Up To Date tick is drawn, because a disabled native checkbox is too faint to tell ticked from empty', () => {
  assert.match(rule('.patch-tick {'), /border:\s*2px solid/);
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
