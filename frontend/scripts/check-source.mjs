// A real parse of the front end, to find the mistakes that only show up when the page is built: syntax errors, a name declared twice ("The
// symbol 'x' has already been declared"), and a name that is used but never declared (a missing import, a typo). It needs TypeScript, once:
//
//     npm install --save-dev typescript
//     npm run check
//
// `npm test` has a smaller check with no installs (src/declarations.test.js); this one finds more.
import { readdirSync } from 'node:fs';
import { join, relative } from 'node:path';

let ts;
try {
  ts = (await import('typescript')).default;
} catch {
  console.error('This check needs TypeScript. Install it once with:  npm install --save-dev typescript');
  process.exit(2);
}

const root = process.argv[2] ?? 'src';
const walk = (dir) => readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]));
const files = walk(root).filter((f) => /\.(jsx?|mjs)$/.test(f) && !f.endsWith('.test.js'));
const program = ts.createProgram(files, {
  allowJs: true, checkJs: true, noEmit: true, noResolve: true, jsx: ts.JsxEmit.Preserve, jsxImportSource: 'preact', strict: false, noImplicitAny: false, skipLibCheck: true,
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, lib: ['lib.es2023.d.ts', 'lib.dom.d.ts', 'lib.dom.iterable.d.ts'], types: [],
});
// redeclared, duplicate, cannot find name (and "did you mean"), and the other ways a name can be wrong; type errors are not wanted here
const wanted = new Set([2451, 2300, 2304, 2552, 2393, 2392, 2395, 2528, 2441, 2454]);
let problems = 0;
for (const file of files) {
  const source = program.getSourceFile(file);
  for (const d of [...program.getSyntacticDiagnostics(source), ...program.getSemanticDiagnostics(source).filter((x) => wanted.has(x.code))]) {
    const { line, character } = source.getLineAndCharacterOfPosition(d.start ?? 0);
    console.log(`${relative(root, file)}:${line + 1}:${character + 1}  TS${d.code}  ${ts.flattenDiagnosticMessageText(d.messageText, ' ')}`);
    problems += 1;
  }
}
console.log(problems ? `${problems} problem(s) in ${files.length} files` : `No syntax errors, duplicate names or undefined names in ${files.length} files.`);
process.exit(problems ? 1 : 0);
