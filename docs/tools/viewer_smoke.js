// Smoke test for the documentation viewer page: runs its JavaScript against a stub DOM and a real running viewer, and checks that the
// main flows (list, open, navigate, search, diagram fallback) complete without errors. It does not test how the page LOOKS: it needs no browser.
//   node docs/tools/viewer_smoke.js http://127.0.0.1:8765
const vm = require('node:vm');
const base = process.argv[2];
process.on('unhandledRejection', (e) => { console.error('UNHANDLED', e); process.exit(2); });

function element() {
  const handlers = {};
  return {
    innerHTML: '', textContent: '', value: '', scrollTop: 0, dataset: {}, open: false, handlers,
    classes: new Set(), classList: null,
    addEventListener(type, fn) { handlers[type] = fn; },
    insertAdjacentHTML(where, html) { this.innerHTML = where === 'afterbegin' ? html + this.innerHTML : this.innerHTML + html; },
    scrollIntoView() { this.scrolled = true; },
    querySelectorAll() { return []; },
  };
}
const elements = {};
const pick = (selector) => elements[selector] || (elements[selector] = element());
const diagramNode = element();
diagramNode.classList = { add: (c) => diagramNode.classes.add(c) };
diagramNode.insertAdjacentHTML = function (where, html) { this.before = (this.before || '') + html; };
const windowHandlers = {};
const document = {
  title: '', hidden: false,
  querySelector: pick,
  querySelectorAll: (selector) => (selector.includes('pre.mermaid') && pick('#content').innerHTML.includes('class="mermaid"') ? [diagramNode] : []),
  getElementById: () => null,
  createElement: () => ({}),
  head: { appendChild(script) { setTimeout(() => script.onerror && script.onerror(), 0); } },   // no Mermaid available: the fallback must work
  addEventListener(type, fn) { windowHandlers['document:' + type] = fn; },
};
const location = { hash: '' };
const context = vm.createContext({
  document, location, console, URL, encodeURI, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, Promise, RegExp, Object, Array, Set, String, JSON,
  setInterval: () => 0, matchMedia: () => ({ matches: false }), Error,
  fetch: (path, opts) => fetch(new URL(path, base), opts),
  addEventListener: (type, fn) => { windowHandlers[type] = fn; },
});
context.window = context;

const html = await_text(base + '/');
function await_text(url) { return fetch(url).then((r) => r.text()); }

(async () => {
  const page = await html;
  const script = /<script>([\s\S]*?)<\/script>/.exec(page)[1];
  vm.runInContext(script, context);
  const settle = async (check, label) => {
    for (let i = 0; i < 60; i++) { if (check()) return; await new Promise((r) => setTimeout(r, 50)); }
    console.error(`TIMEOUT waiting for: ${label}\n#content: ${pick('#content').innerHTML.slice(0, 300)}`); process.exit(3);
  };
  const go = async (hash, check, label) => { location.hash = hash; windowHandlers.hashchange(); await settle(check, label); };

  await settle(() => pick('#tree').innerHTML.includes('<a href="#/README.md"'), 'the document list');
  await settle(() => pick('#content').innerHTML.includes('<article>'), 'the first document opens by itself');
  const results = { listed: (pick('#tree').innerHTML.match(/<a href=/g) || []).length };

  await go('#/05_OPERATIONS.md', () => pick('#content').innerHTML.includes('Operations, deployment'), 'navigate to another document');
  results.toc = pick('#toc').innerHTML.includes('class="l2"');

  await go('#/diagrams/er_cove.mmd', () => pick('#content').innerHTML.includes('class="mermaid"'), 'open a diagram');
  await settle(() => diagramNode.classes.has('failed'), 'the diagram falls back to showing its source when Mermaid cannot load');
  results.diagramFallback = pick('#content').innerHTML.includes('shown as source');

  await go('#?q=heartbeat', () => pick('#content').innerHTML.includes('document(s) match'), 'search');
  results.search = pick('#content').innerHTML.includes('<mark>');

  await go('#/diagrams/er_cove.png', () => pick('#content').innerHTML.includes('<img src="/files/diagrams/er_cove.png"'), 'open an image');
  await go('#/IP_Capital_Technical_Handover.pdf', () => pick('#content').innerHTML.includes('<iframe'), 'open a PDF');
  await go('#/IP_Capital_Technical_Handover.docx', () => pick('#content').innerHTML.includes('Download'), 'offer a Word file as a download');
  await go('#/does-not-exist.md', () => pick('#content').innerHTML.includes('Could not open'), 'a missing file is reported');
  results.ok = true;
  console.log('OK ' + JSON.stringify(results));
  process.exit(0);
})();
