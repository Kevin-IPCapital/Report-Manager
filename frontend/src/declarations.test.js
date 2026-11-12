import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

// A name declared twice in the same scope is a build error ("The symbol 'x' has already been declared") that no other test can see, because
// the tests never load the pages. This finds it without any tool: it reads the declarations at the two levels where it happens, the top of a
// file and the top of a function (this code is always written with two-space indents), and reports any name that appears twice.
// `npm run check` does the same with a real parser, and more, if TypeScript is installed.

const split = (text) => {   // split on commas that are not inside brackets
  const parts = []; let depth = 0, current = '';
  for (const ch of text) {
    if ('{[('.includes(ch)) depth += 1;
    if ('}])'.includes(ch)) depth -= 1;
    if (ch === ',' && depth === 0) { parts.push(current); current = ''; } else current += ch;
  }
  return [...parts, current].map((p) => p.trim()).filter(Boolean);
};
const nameOf = (part) => {   // `a`, `a = 1`, `a: b`, `...rest`  ->  the name that is declared
  const name = part.replace(/^\.\.\./, '').split('=')[0].split(':').pop().trim();
  return /^[A-Za-z_$][\w$]*$/.test(name) ? name : null;
};
const pattern = (text) => {   // the names in `a`, `{ a, b: c }`, `[a, setA]`
  const t = text.trim();
  if (t.startsWith('{') || t.startsWith('[')) return split(t.slice(1, t.lastIndexOf(t[0] === '{' ? '}' : ']'))).map(nameOf).filter(Boolean);
  const single = nameOf(t);
  return single ? [single] : [];
};
// What is declared by the start of `const ...` or `let ...` (the text after the keyword): the name, or the whole { } or [ ] pattern (found by
// matching brackets, since a default value inside it can contain an `=`), when it is followed by `=`.
const declared = (rest) => {
  const t = rest.trimStart();
  let end = 0;
  if (t[0] === '{' || t[0] === '[') {
    let depth = 0;
    for (; end < t.length; end += 1) {
      if ('{[('.includes(t[end])) depth += 1;
      if ('}])'.includes(t[end])) { depth -= 1; if (depth === 0) { end += 1; break; } }
    }
  } else {
    end = t.search(/[\s=;]/);
    if (end < 0) end = t.length;
  }
  return /^\s*=(?!=)/.test(t.slice(end)) ? pattern(t.slice(0, end)) : [];
};

export function duplicates(code) {
  const lines = code.split('\n'), found = [];
  const check = (names, where) => {
    const seen = new Set();
    for (const { name, line } of names) {
      if (seen.has(name)) found.push(`${name} (line ${line}, ${where})`); else seen.add(name);
    }
  };
  // the top of the file: imports, functions, classes, constants
  const top = [];
  lines.forEach((text, i) => {
    let m = text.match(/^import\s+(.*?)\s+from\s/);
    if (m) {
      const clause = m[1];
      const braces = clause.match(/\{([^}]*)\}/);
      if (braces) for (const part of split(braces[1])) { const n = part.split(/\s+as\s+/).pop().trim(); if (n) top.push({ name: n, line: i + 1 }); }
      const rest = clause.replace(/\{[^}]*\}/, '').replace(/\*\s+as\s+/, '').replace(/,/g, ' ').trim();
      for (const n of rest.split(/\s+/).filter(Boolean)) top.push({ name: n, line: i + 1 });
      return;
    }
    m = text.match(/^(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function\*?|class)\s+([A-Za-z_$][\w$]*)/);
    if (m) { top.push({ name: m[1], line: i + 1 }); return; }
    m = text.match(/^(?:export\s+)?(?:const|let)\s+(.*)$/);
    if (m) for (const name of declared(m[1])) top.push({ name, line: i + 1 });
  });
  check(top, 'top of the file');
  // the top of each function: its parameters and its two-space-indented const and let
  for (let i = 0; i < lines.length; i += 1) {
    const header = lines[i].match(/^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\*?\s*([A-Za-z_$][\w$]*)?\s*\((.*)\)\s*\{\s*$/);
    if (!header) continue;
    const names = split(header[2]).flatMap(pattern).map((name) => ({ name, line: i + 1 }));
    let end = i + 1;
    while (end < lines.length && !lines[end].startsWith('}')) {
      const m = lines[end].match(/^ {2}(?:const|let)\s+(.*)$/);
      if (m) for (const name of declared(m[1])) names.push({ name, line: end + 1 });
      end += 1;
    }
    check(names, `in ${header[1] ?? 'a function'}`);
    i = end;
  }
  return found;
}

test('the same name declared twice at the top of a function is found, as it was in the trend panel', () => {
  const code = ['function TrendPanel({ filter }) {', '  const { data } = useApi();', '  const { ranges: saved } = useSavedRanges();', '  const shown = limit(data, saved);',
    '  const saved = data.y.length > 0;', '  return null;', '}'].join('\n');
  assert.deepEqual(duplicates(code), ['saved (line 5, in TrendPanel)']);
});

test('a parameter declared again inside the function is found', () => {
  assert.deepEqual(duplicates('function f({ items, size }) {\n  const size = 3;\n  return items;\n}'), ['size (line 2, in f)']);
  assert.deepEqual(duplicates('function f(a, b) {\n  let a = 1;\n}'), ['a (line 2, in f)']);
});

test('names declared twice at the top of a file are found, including an import', () => {
  assert.deepEqual(duplicates("import { a } from './a';\nconst a = 1;\n"), ['a (line 2, top of the file)']);
  assert.deepEqual(duplicates("import x from './x';\nimport { y as x } from './y';\n"), ['x (line 2, top of the file)']);
  assert.deepEqual(duplicates('function go() {\n}\nexport function go() {\n}\n'), ['go (line 3, top of the file)']);
  assert.deepEqual(duplicates('const pct = 1;\nexport const pct = 2;\n'), ['pct (line 2, top of the file)']);
});

test('the same name in different functions, or inside and outside one, is fine', () => {
  const code = ['const total = 1;', 'function a() {', '  const total = 2;', '  return total;', '}', 'function b() {', '  const total = 3;', '  return total;', '}'].join('\n');
  assert.deepEqual(duplicates(code), []);
});

test('names in nested blocks, strings and comparisons are not mistaken for declarations', () => {
  const code = ['function f(x) {', '  const a = x === 1 ? 1 : 2;', '  if (x) {', '    const a = 5;', '  }', "  const message = 'const a = 1';", '  return a;', '}'].join('\n');
  assert.deepEqual(duplicates(code), []);
});

test('destructuring, renames, defaults and rest are all read', () => {
  assert.deepEqual(duplicates('function f() {\n  const { a, b: c, d = 1, ...rest } = x;\n  const [e, setE] = y;\n  const c = 1;\n}'), ['c (line 4, in f)']);
  assert.deepEqual(duplicates('function f() {\n  const { a, b: c, d = 1, ...rest } = x;\n  const rest = 1;\n}'), ['rest (line 3, in f)']);
});

const root = fileURLToPath(new URL('.', import.meta.url));
const walk = (dir) => readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]));

test('no source file declares a name twice at the top of the file or the top of a function', () => {
  const files = walk(root).filter((f) => /\.jsx?$/.test(f) && !f.endsWith('.test.js'));
  assert.ok(files.length > 50);
  const problems = files.flatMap((f) => duplicates(readFileSync(f, 'utf8')).map((d) => `${f.slice(root.length).replaceAll('\\', '/')}: ${d}`));
  assert.deepEqual(problems, []);
});
