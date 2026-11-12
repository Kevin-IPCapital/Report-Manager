"""The documentation in ../docs must keep up with the code. These tests fail when a route, a setting or a job is added
without being written down. They are skipped if the docs folder is not deployed with this copy."""
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from api.collection_freshness import SOURCE_QUERIES
from api.collection_health_core import JOB_NAMES
from api.management.commands.run_collection import JOB_COMMANDS, NO_DRY_RUN

BACKEND = Path(settings.BASE_DIR)
DOCS = BACKEND.parent / 'docs'


def doc_path(route):
    """'api/sections/<str:name>' -> '/api/sections/{name}'"""
    return '/' + re.sub(r'<int:pk>', '{id}', re.sub(r'<str:(\w+)>', r'{\1}', route))


class DocumentationMatchesCode(SimpleTestCase):
    def doc(self, name):
        path = DOCS / name
        if not path.is_file():
            self.skipTest(f'{name} is not deployed with this copy')
        return path.read_text(encoding='utf-8')

    def routes(self):
        return re.findall(r"path\('(api/[^']+)'", (BACKEND / 'config' / 'urls.py').read_text(encoding='utf-8'))

    def test_every_route_is_in_the_api_reference(self):
        text = self.doc('08_API_REFERENCE.md')
        missing = [doc_path(r) for r in self.routes() if doc_path(r) not in text]
        self.assertEqual(missing, [], 'Routes missing from docs/08_API_REFERENCE.md')

    def test_the_api_reference_lists_no_route_that_does_not_exist(self):
        text = self.doc('08_API_REFERENCE.md')
        rows = re.findall(r'^\| (?:GET|POST|PUT|DELETE) \| `(/api/[^`]+)`', text, re.M)
        listed = {re.sub(r'^/api/sections/[a-z-]+$', '/api/sections/{name}', p) for p in rows}   # one route serves every section
        real = {doc_path(r) for r in self.routes()}
        self.assertEqual(sorted(listed - real), [], 'Documented routes that are not in config/urls.py')
        self.assertEqual(sorted(real - listed), [], 'Routes missing from the summary table')

    def test_every_report_section_has_a_heading_in_the_api_reference(self):
        text = self.doc('08_API_REFERENCE.md')
        source = (BACKEND / 'api' / 'data' / '__init__.py').read_text(encoding='utf-8')
        names = re.findall(r"^\s{8}'([a-z-]+)':", source, re.M)
        self.assertTrue(names)
        self.assertEqual([n for n in names if f'### `{n}`' not in text], [])

    def test_every_environment_variable_is_documented(self):
        text = self.doc('05_OPERATIONS.md')
        source = (BACKEND / 'config' / 'settings.py').read_text(encoding='utf-8')
        names = set(re.findall(r"os\.environ\.get\(\s*'([A-Z_]+)'", source)) | set(re.findall(r"os\.environ\[\s*'([A-Z_]+)'", source))
        self.assertTrue(names)
        self.assertEqual(sorted(n for n in names if f'`{n}`' not in text), [], 'Variables missing from docs/05_OPERATIONS.md')

    def test_no_document_names_a_backlog_item_without_saying_what_it_is(self):
        """A line that mentions an item such as OVER-05 must say what it is in plain words (at least nine other words), and every item it
        names must exist in the backlog. The full text of an item lives in 06_STATUS_AND_BACKLOG.md; everywhere else it is described, not just named."""
        backlog = self.doc('06_STATUS_AND_BACKLOG.md')
        ids = set(re.findall(r'^\| \**([A-Z]{2,7}-\d{2})\**\s*\|', backlog, re.M))
        self.assertGreater(len(ids), 50)
        pattern = re.compile(r'\b(?:' + '|'.join(sorted({i.split('-')[0] for i in ids})) + r')-\d{2}\b')
        unknown, bare = [], []
        for path in sorted(DOCS.glob('*.md')) + [BACKEND.parent / 'README.md']:
            if path.name in ('06_STATUS_AND_BACKLOG.md', 'SOURCE_INDEX.md') or not path.is_file():
                continue
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                found = pattern.findall(line)
                if not found:
                    continue
                unknown += [f'{path.name}:{number} {i}' for i in found if i not in ids]
                words = re.sub(r'[|*`#\-\[\]()]', ' ', pattern.sub('', line)).split()
                if len(words) < 9:
                    bare.append(f'{path.name}:{number} {line.strip()[:90]}')
        self.assertEqual(unknown, [], 'named items that are not in the backlog')
        self.assertEqual(bare, [], 'items named without saying what they are')

    def test_no_obsolete_file_is_left_on_disk(self):
        """Unzipping an archive never deletes files, so a file that a newer archive dropped stays behind and then confuses the other tests (a
        file that is on disk but described nowhere). The list is the one in docs/05_OPERATIONS.md, under "Files an archive does not delete"."""
        listed = obsolete_files()
        self.assertTrue(listed, 'could not read the list of obsolete files from docs/05_OPERATIONS.md')
        left = [p for p in listed if (BACKEND.parent / p).exists()]
        self.assertEqual(left, [], 'Delete these old files by hand, they are not used any more: ' + ', '.join(left))

    def test_every_collector_job_is_documented(self):
        for name in ('05_OPERATIONS.md', '08_API_REFERENCE.md'):
            text = self.doc(name)
            self.assertEqual([job for job in JOB_NAMES if job not in text], [], name)

    def test_every_page_route_is_in_the_page_table(self):
        text = self.doc('03_CODEBASE_AND_API.md')
        routes = re.findall(r"path: '(/[^']*)'", (BACKEND.parent / 'frontend' / 'src' / 'routes.js').read_text(encoding='utf-8'))
        self.assertTrue(routes)
        self.assertEqual([r for r in routes if r != '/' and f'`{r}`' not in text], [])

    def test_every_management_command_is_documented(self):
        text = self.doc('05_OPERATIONS.md')
        commands = [p.stem for p in (BACKEND / 'api' / 'management' / 'commands').glob('*.py')
                    if p.stem not in ('__init__', 'collection_outcomes')]   # collection_outcomes is a library, not a command
        self.assertEqual([c for c in commands if c not in text], [])

    def test_the_pack_has_all_its_files(self):
        for name in ('README.md', '01_SYSTEM_OVERVIEW.md', '02_FLOWCHARTS.md', '03_CODEBASE_AND_API.md', '04_DATA_MODEL.md',
                     '05_OPERATIONS.md', '06_STATUS_AND_BACKLOG.md', '07_VERIFICATION_CHECKLIST.md', '08_API_REFERENCE.md',
                     '09_ER_DIAGRAMS.md', '10_FRONTEND_GUIDE.md', '11_COLLECTORS.md', 'SOURCE_INDEX.md', 'STAGED_PLAN.md', 'serve.py', 'Open-Docs.cmd'):
            self.doc(name)


FRONTEND = BACKEND.parent / 'frontend' / 'src'
COLLECTORS = BACKEND.parent / 'Collector'


def obsolete_files():
    """The files the operations guide says an archive does not delete (paths from the repository root), read from that guide."""
    path = DOCS / '05_OPERATIONS.md'
    if not path.is_file():
        return []
    match = re.search(r'Delete these by hand[^\n]*\n(.*?)\nNever delete', path.read_text(encoding='utf-8'), re.S)
    return sorted(set(re.findall(r'`([\w./-]+\.\w+)`', match.group(1)))) if match else []


def minutes_text(minutes):
    """1440 -> '24 hours', 60 -> '1 hour', 10080 -> '7 days': how the collectors guide words an interval."""
    if minutes == 1440:
        return '24 hours'
    if minutes % 1440 == 0:
        return f'{minutes // 1440} days'
    return '1 hour' if minutes == 60 else f'{minutes // 60} hours' if minutes % 60 == 0 else f'{minutes} minutes'


class FrontEndGuideMatchesCode(SimpleTestCase):
    """docs/10_FRONTEND_GUIDE.md must keep up with the front end. These fail when a file, page, call, remembered setting or component is
    added without being written down."""

    def guide(self):
        path = DOCS / '10_FRONTEND_GUIDE.md'
        if not path.is_file() or not FRONTEND.is_dir():
            self.skipTest('the front end guide or the front end is not deployed with this copy')
        return path.read_text(encoding='utf-8')

    def sources(self, suffixes=('.js', '.jsx')):
        return sorted(p for p in FRONTEND.rglob('*') if p.is_file() and p.suffix in suffixes and not p.name.endswith('.test.js') and 'fixtures' not in p.parts)

    def test_every_source_file_is_in_the_file_index(self):
        text = self.guide()
        files = [p.relative_to(FRONTEND).as_posix() for p in self.sources(('.js', '.jsx', '.css', '.json')) if f'frontend/src/{p.relative_to(FRONTEND).as_posix()}' not in obsolete_files()]   # a leftover is reported once, by its own test
        self.assertTrue(files)
        self.assertEqual([f for f in files if f'`src/{f}`' not in text], [])

    def test_the_file_index_names_no_file_that_does_not_exist(self):
        text = self.guide()
        listed = re.findall(r'^\| `src/([^`]+)` \|', text, re.M)
        self.assertTrue(listed)
        self.assertEqual([f for f in listed if not (FRONTEND / f).is_file()], [])

    def test_every_page_route_is_in_the_guide(self):
        text = self.guide()
        routes = re.findall(r"path: '(/[^']*)'", (FRONTEND / 'routes.js').read_text(encoding='utf-8'))
        self.assertEqual([r for r in routes if f'`{r}`' not in text], [])

    def test_every_api_call_is_in_the_guide(self):
        text = self.guide()
        source = (FRONTEND / 'api' / 'adapter.js').read_text(encoding='utf-8')
        block = source[source.index('export const api = {'):]
        names = re.findall(r'^  (?:async )?([a-zA-Z]+)[:(]', block, re.M)
        self.assertTrue(len(names) > 15)
        self.assertEqual([n for n in names if f'api.{n}' not in text], [])

    def test_every_remembered_setting_is_in_the_guide(self):
        text = self.guide()
        keys = set()
        for path in self.sources():
            code = path.read_text(encoding='utf-8')
            keys |= set(re.findall(r"usePersistentState\('([^']+)'", code))
            keys |= set(re.findall(r"usePersisted\('(ops_[a-z_]+)'", code))
            keys |= {f'table:{name}' for name in re.findall(r'persistKey="([a-z-]+)"', code)} | {f'{name}:filters' for name in re.findall(r'persistKey="([a-z-]+)"', code) if 'TicketExplorer' in code}
        self.assertTrue(len(keys) > 10)
        # a table's name is in the guide as `<name>` inside the table row, and a ticket page's filters as `<name>:filters`
        missing = [k for k in sorted(keys) if (k.split(':', 1)[1] if k.startswith('table:') else k) not in text]
        self.assertEqual(missing, [])

    def test_every_component_is_in_the_guide(self):
        text = self.guide()
        names = []
        for path in sorted(p for p in (FRONTEND / 'components').glob('*.js*') if f'frontend/src/components/{p.name}' not in obsolete_files()):
            names += re.findall(r'^export function ((?:use)?[A-Z]\w*|use[A-Z]\w*)', path.read_text(encoding='utf-8'), re.M)
        self.assertTrue(len(names) > 20)
        self.assertEqual([n for n in names if f'`{n}`' not in text], [])

    def test_every_pure_module_with_a_test_says_so(self):
        text = self.guide()
        tests = [p.name for p in FRONTEND.glob('*.test.js')]
        self.assertTrue(tests)
        self.assertEqual([t for t in tests if t not in text and t.replace('.test.js', '') + '.test.js' not in text], [])

    def test_the_guide_points_at_the_test_that_guards_the_graphs(self):
        self.assertIn('charts.test.js', self.guide())


class CollectorsGuideMatchesCode(SimpleTestCase):
    """docs/11_COLLECTORS.md must keep up with the collectors and the way the dashboard runs them."""

    def guide(self):
        path = DOCS / '11_COLLECTORS.md'
        if not path.is_file() or not COLLECTORS.is_dir():
            self.skipTest('the collectors guide or the collectors are not deployed with this copy')
        return path.read_text(encoding='utf-8')

    def schemas(self):
        path = DOCS / 'tools' / 'generate_er.py'
        if not path.is_file():
            self.skipTest('docs/tools is not deployed with this copy')
        spec = importlib.util.spec_from_file_location('generate_er', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_every_collector_folder_is_in_the_guide(self):
        text = self.guide()
        folders = [p.name for p in COLLECTORS.iterdir() if p.is_dir()]
        self.assertEqual(len(folders), 4)
        self.assertEqual([f for f in folders if f'Collector/{f}' not in text], [])

    def test_every_job_and_its_script_is_in_the_guide(self):
        text = self.guide()
        self.assertEqual(sorted(JOB_COMMANDS), sorted(JOB_NAMES))
        self.assertEqual([j for j in JOB_NAMES if f'`{j}`' not in text], [])
        for job, command in JOB_COMMANDS.items():
            self.assertIn(' '.join(command[1:]), text, job)                       # the script and its arguments, as the table words them

    def test_the_dry_run_column_matches_the_jobs_that_refuse_one(self):
        text = self.guide()
        for job in JOB_COMMANDS:
            row = re.search(rf'^\| `{job}` \| `\w+` \|[^\n]*\| (supported|refused[^|]*) \|$', text, re.M)
            self.assertIsNotNone(row, job)
            self.assertEqual('refused' in row.group(1), job in NO_DRY_RUN, job)

    def test_the_shipped_defaults_table_matches_the_example_settings(self):
        text = self.guide()
        example = json.loads((BACKEND / 'collector_jobs.example.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted(example['jobs']), sorted(JOB_NAMES))
        for job, config in example['jobs'].items():
            row = re.search(rf'^\| `{job}` \| (yes|no) \| (yes|no) \| ([^|]+?) \| ([^|]+?) \|$', text, re.M)
            self.assertIsNotNone(row, job)
            self.assertEqual(row.groups(), ('yes' if config['enabled'] else 'no', 'yes' if config['schedule_enabled'] else 'no',
                                            minutes_text(config['interval_minutes']), f"{config['timeout_seconds'] // 60} minutes"), job)

    def test_every_freshness_fallback_is_in_the_guide(self):
        text = self.guide()
        self.assertEqual(sorted(SOURCE_QUERIES), sorted(JOB_NAMES))
        for job, query in SOURCE_QUERIES.items():
            column = re.search(r'(?:MAX\()(\w+)\)? FROM (\w+\.\w+)', query)
            self.assertIsNotNone(column, job)
            self.assertIn(f'`{column.group(2)}.{column.group(1)}`', text, job)

    def test_every_setting_a_collector_reads_is_in_the_guide(self):
        text = self.guide()
        names = set()
        for path in COLLECTORS.rglob('*.py'):
            names |= set(re.findall(r"['\"]((?:NCENTRAL|AUTOTASK|SENTINELONE|S1|IPC|COVE)_[A-Z0-9_]+)['\"]", path.read_text(encoding='utf-8')))
        self.assertTrue(len(names) >= 15)
        self.assertEqual([n for n in sorted(names) if f'`{n}`' not in text], [])

    def test_every_table_every_collector_fills_is_in_the_guide(self):
        text = self.guide()
        generator = self.schemas()
        missing = []
        for schema in ('ncentral', 'autotask', 'sentinelone', 'cove'):
            tables, _ = generator.load(schema)
            self.assertTrue(tables, schema)
            missing += [f'{schema}.{t}' for t in sorted(tables) if f'`{schema}.{t}`' not in text]
        self.assertEqual(missing, [])

    def test_the_steps_for_adding_a_job_name_the_places_that_must_change(self):
        text = self.guide()
        for place in ('JOB_COMMANDS', 'NO_DRY_RUN', 'JOB_NAMES', 'JOB_LABELS', 'SOURCE_QUERIES', 'collector_jobs.example.json', 'CollectionHealth.jsx'):
            self.assertIn(place, text)
        # and those places really exist, so the steps are not out of date
        self.assertIn('NAMES', (FRONTEND / 'pages' / 'CollectionHealth.jsx').read_text(encoding='utf-8'))


class ErDiagramsMatchDefinitions(SimpleTestCase):
    def generator(self):
        path = DOCS / 'tools' / 'generate_er.py'
        if not path.is_file():
            self.skipTest('docs/tools is not deployed with this copy')
        spec = importlib.util.spec_from_file_location('generate_er', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_er_files_are_up_to_date(self):
        stale = [path for path, text in self.generator().render_all().items()
                 if not (DOCS / path).is_file() or (DOCS / path).read_text(encoding='utf-8') != text]
        self.assertEqual(stale, [], 'Out of date: run `python docs/tools/generate_er.py`')

    def test_every_table_in_every_schema_has_a_row(self):
        generator = self.generator()
        text = (DOCS / '09_ER_DIAGRAMS.md').read_text(encoding='utf-8')
        for key, _path, _name in generator.SCHEMAS:
            tables, _views = generator.load(key)
            self.assertTrue(tables, key)
            self.assertEqual([t for t in tables if f'| `{t}` |' not in text], [], key)
        models, _ = generator.load('report_manager')
        self.assertEqual([t for t in models if f'| `{t}` |' not in text], [])

    def test_the_diagrams_refer_only_to_columns_that_exist(self):
        # render_all() exits if a dashed (unenforced) link or the cross-schema diagram names a column that is not in the definitions
        self.assertTrue(self.generator().render_all())

    def test_the_images_exist(self):
        generator = self.generator()
        missing = [f'er_{key}.{ext}' for key in [k for k, _p, _n in generator.SCHEMAS] + ['report_manager'] for ext in ('png', 'svg')
                   if not (DOCS / 'diagrams' / f'er_{key}.{ext}').is_file()]
        self.assertEqual(missing, [])


def load_viewer(test):
    path = DOCS / 'serve.py'
    if not path.is_file():
        test.skipTest('docs/serve.py is not deployed with this copy')
    spec = importlib.util.spec_from_file_location('docs_serve', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MarkdownRenderer(SimpleTestCase):
    def setUp(self):
        self.v = load_viewer(self)

    def render(self, text, path='doc.md', exists=lambda p: True):
        return self.v.render_markdown(text, path, exists)

    def test_headings_get_ids_a_contents_list_and_duplicates_are_told_apart(self):
        body, toc, _ = self.render('# Title\n\n## Same\n\n## Same\n\n### `code` and **bold**\n')
        self.assertEqual([(h['level'], h['text'], h['id']) for h in toc],
                         [(1, 'Title', 'title'), (2, 'Same', 'same'), (2, 'Same', 'same-2'), (3, 'code and bold', 'code-and-bold')])
        self.assertIn('<h2 id="same-2">Same</h2>', body)

    def test_nothing_typed_can_inject_html_but_br_is_allowed(self):
        body, _, _ = self.render('<script>alert(1)</script> and <img src=x onerror=y>\n\nline<br>two\n\n`<b>`')
        self.assertNotIn('<script>', body)
        self.assertNotIn('<img', body)
        self.assertIn('&lt;script&gt;', body)
        self.assertIn('line<br>two', body)
        self.assertIn('<code>&lt;b&gt;</code>', body)

    def test_inline_formatting(self):
        body, _, _ = self.render('**bold** and *italic* and `a_b_c` and snake_case_word and 5 * 3 * 2')
        self.assertIn('<strong>bold</strong>', body)
        self.assertIn('<em>italic</em>', body)
        self.assertIn('<code>a_b_c</code>', body)
        self.assertIn('snake_case_word', body)       # underscores inside words are not emphasis
        self.assertNotIn('<em> 3 </em>', body)       # "5 * 3 * 2" is arithmetic

    def test_code_blocks_are_escaped_and_headings_inside_them_are_not_headings(self):
        body, toc, _ = self.render('```sql\n# not a heading\nSELECT a < b;\n```\n\n## Real\n')
        self.assertEqual([h['text'] for h in toc], ['Real'])
        self.assertIn('<pre class="code" data-lang="sql"><code># not a heading\nSELECT a &lt; b;</code></pre>', body)

    def test_mermaid_blocks_are_marked_for_drawing(self):
        body, _, _ = self.render('```mermaid\nflowchart TD\n  A --> B\n```')
        self.assertIn('<pre class="mermaid">flowchart TD\n  A --&gt; B</pre>', body)

    def test_tables(self):
        body, _, _ = self.render('| A | B | C |\n|:--|:-:|--:|\n| `x | y` | 1\\|2 | a<br>b |\n| short |\n')
        self.assertEqual(body.count('<tr>'), 3)
        self.assertIn('<code>x | y</code>', body)                # a pipe inside backticks stays in the cell
        self.assertIn('1|2', body)                                 # an escaped pipe too
        self.assertIn('a<br>b', body)
        self.assertIn('<th style="text-align:center">B</th>', body)
        self.assertIn('<td style="text-align:right"></td>', body)  # a short row is padded

    def test_lists_nest_and_numbered_lists_keep_their_start(self):
        body, _, _ = self.render('- one\n  - nested\n- two\n\n3. three\n4. four\n')
        self.assertIn('<ul><li>one<ul><li>nested</li></ul></li><li>two</li></ul>', body)
        self.assertIn('<ol start=3><li>three</li><li>four</li></ol>', body)

    def test_a_list_can_follow_a_paragraph_directly_and_items_can_continue(self):
        body, _, _ = self.render('Intro:\n- item that\n  continues\n- next\n')
        self.assertIn('<p>Intro:</p>', body)
        self.assertIn('<li>item that continues</li>', body)

    def test_blockquote_rule_and_front_matter(self):
        body, toc, _ = self.render('---\ntitle: "Hidden"\n---\n\n> quoted *text*\n\n---\n\nafter')
        self.assertNotIn('Hidden', body)
        self.assertIn('<blockquote><p>quoted <em>text</em></p></blockquote>', body)
        self.assertIn('<hr>', body)

    def test_links_between_documents_stay_inside_the_viewer(self):
        body, _, links = self.render('[ops](05_OPERATIONS.md) [part](05_OPERATIONS.md#alerts) [pic](diagrams/a.png) [here](#top)', 'docs/sub/x.md')
        self.assertIn('href="#/docs/sub/05_OPERATIONS.md"', body)
        self.assertIn('href="#/docs/sub/05_OPERATIONS.md#alerts"', body)
        self.assertIn('href="#/docs/sub/x.md#top"', body)
        self.assertEqual({l['target'] for l in links}, {'docs/sub/05_OPERATIONS.md', 'docs/sub/diagrams/a.png'})

    def test_a_link_to_a_missing_file_is_marked(self):
        body, _, links = self.render('[gone](nope.md)', 'a.md', exists=lambda p: False)
        self.assertIn('class="broken"', body)
        self.assertEqual([l['exists'] for l in links], [False])

    def test_external_links_open_in_a_new_tab_and_unsafe_ones_are_dead(self):
        body, _, _ = self.render('[a](https://example.com/x?a=1&b=2) [b](javascript:alert(1)) [c](data:text/html,x) [d](mailto:a@b.c)')
        self.assertIn('href="https://example.com/x?a=1&amp;b=2" target="_blank" rel="noopener noreferrer"', body)
        self.assertNotIn('javascript:', body)
        self.assertNotIn('data:text', body)
        self.assertIn('href="mailto:a@b.c"', body)

    def test_links_cannot_climb_out_of_the_folder(self):
        _, _, links = self.render('[x](../../etc/passwd)', 'a.md')
        self.assertEqual([l['exists'] for l in links], [False])

    def test_images_load_through_the_file_route_and_pandoc_sizes_are_dropped(self):
        body, _, _ = self.render('![Alt text](diagrams/a.png){width=6in} and ![remote](https://example.com/p.png)', 'x.md')
        self.assertIn('<img src="/files/diagrams/a.png" alt="Alt text" loading="lazy">', body)
        self.assertNotIn('{width', body)
        self.assertIn('src="https://example.com/p.png"', body)

    def test_every_document_in_the_pack_renders_completely(self):
        for item in self.v.scan():
            if item['kind'] != 'markdown':
                continue
            text = (DOCS / item['path']).read_text(encoding='utf-8')
            body, toc, links = self.v.render_markdown(text, item['path'], lambda p: (DOCS / p).is_file())
            outside = re.sub(r'(```|~~~).*?\1', '', self.v.front_matter(text)[1], flags=re.S)
            self.assertEqual(len(toc), len(re.findall(r'^\s{0,3}#{1,6}\s+\S', outside, re.M)), f"{item['path']}: headings")
            separators = re.findall(r'^\s*\|?\s*:?-+:?\s*\|[\s|:-]*$', outside, re.M)   # a table's |---|---| line (a horizontal rule has no pipe)
            self.assertEqual(body.count('<table>'), len(separators), f"{item['path']}: tables")
            self.assertEqual(body.count('<pre class="mermaid">'), len(re.findall(r'^```mermaid', text, re.M)), f"{item['path']}: diagrams")
            self.assertNotIn('|---', body, f"{item['path']}: a table was not recognised")
            self.assertNotIn('```', body, f"{item['path']}: a code block was not closed")
            self.assertNotIn('\ue000', body, item['path'])
            self.assertEqual([l['url'] for l in links if not l['exists']], [], f"{item['path']}: broken link")

    def test_no_link_in_the_documentation_is_broken(self):
        self.assertEqual(self.v.check_links(), [])


class DocumentationViewer(SimpleTestCase):
    def setUp(self):
        self.v = load_viewer(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'README.md').write_text('# Home\n\nSee [guide](guide.md).\n', encoding='utf-8')
        (self.root / 'guide.md').write_text('# Guide\n\n## Setup\n\nInstall the heartbeat worker.\n\n```sql\n## hidden heartbeat\n```\n', encoding='utf-8')

    def names(self):
        return [i['path'] for i in self.v.scan(self.root)]

    def test_a_new_file_appears_without_restarting(self):
        first = self.v.signature(self.v.scan(self.root))
        self.assertEqual(self.names(), ['guide.md', 'README.md'])
        (self.root / 'notes.md').write_text('# Notes\n', encoding='utf-8')
        sub = self.root / 'diagrams'
        sub.mkdir()
        (sub / 'flow.mmd').write_text('flowchart TD\n A-->B\n', encoding='utf-8')
        self.assertIn('notes.md', self.names())
        self.assertIn('diagrams/flow.mmd', self.names())
        self.assertNotEqual(first, self.v.signature(self.v.scan(self.root)))

    def test_an_edit_changes_the_signature_and_titles_come_from_the_heading(self):
        before = self.v.signature(self.v.scan(self.root))
        (self.root / 'guide.md').write_text('# Renamed guide\n\nlonger content here\n', encoding='utf-8')
        self.assertNotEqual(before, self.v.signature(self.v.scan(self.root)))
        self.assertIn('Renamed guide', [i['title'] for i in self.v.scan(self.root)])

    def test_kinds_hidden_files_and_the_viewer_itself(self):
        for name, text in (('a.png', 'x'), ('b.pdf', 'x'), ('c.docx', 'x'), ('d.sql', 'x'), ('.secret', 'x'), ('serve.py', 'x'), ('Open-Docs.cmd', 'x'), ('e.bin', 'x')):
            (self.root / name).write_text(text)
        (self.root / 'vendor').mkdir()
        (self.root / 'vendor' / 'mermaid.min.js').write_text('x')
        (self.root / '__pycache__').mkdir()
        (self.root / '__pycache__' / 'x.pyc').write_text('x')
        kinds = {i['path']: i['kind'] for i in self.v.scan(self.root)}
        self.assertEqual(kinds, {'guide.md': 'markdown', 'README.md': 'markdown', 'a.png': 'image', 'b.pdf': 'pdf', 'c.docx': 'download', 'd.sql': 'text', 'e.bin': 'other'})

    def test_files_are_listed_in_natural_order(self):
        for name in ('10_b.md', '2_a.md', '1_z.md'):
            (self.root / name).write_text('# x\n')
        self.assertEqual([n for n in self.names() if n[0].isdigit()], ['1_z.md', '2_a.md', '10_b.md'])

    def test_paths_cannot_escape_the_folder(self):
        outside = self.root.parent / 'outside-secret.txt'
        outside.write_text('secret')
        self.addCleanup(outside.unlink)
        (self.root / '.hidden.md').write_text('# h')
        (self.root / '.git').mkdir()
        (self.root / '.git' / 'config').write_text('x')
        for attempt in ('../outside-secret.txt', '..%2foutside-secret.txt', '%2e%2e/outside-secret.txt', '..\\outside-secret.txt',
                        str(outside), '/' + str(outside), 'guide.md/../../outside-secret.txt', '.hidden.md', '.git/config', '', 'nope.md', 'a\x00b'):
            self.assertIsNone(self.v.resolve(attempt, self.root), attempt)
        self.assertIsNotNone(self.v.resolve('guide.md', self.root))
        self.assertIsNotNone(self.v.resolve('%67uide.md', self.root))
        try:
            (self.root / 'link.txt').symlink_to(outside)
        except (OSError, NotImplementedError):
            return
        self.assertIsNone(self.v.resolve('link.txt', self.root), 'a link that leads outside the folder')

    def test_search_needs_every_word_finds_the_heading_and_skips_code_headings(self):
        results = self.v.search('heartbeat worker', self.root)
        self.assertEqual([r['path'] for r in results], ['guide.md'])
        self.assertEqual(results[0]['matches'][0]['anchor'], 'setup')
        self.assertEqual([m['anchor'] for m in self.v.search('hidden', self.root)[0]['matches']], ['setup'])   # the "## hidden" inside a code block is not a heading
        self.assertEqual(self.v.search('heartbeat nonsense', self.root), [])
        self.assertEqual(self.v.search('   ', self.root), [])

    def test_search_ranks_a_title_match_first(self):
        (self.root / 'a.md').write_text('# Other\n\nmentions guide once\n', encoding='utf-8')
        self.assertEqual(self.v.search('guide', self.root)[0]['path'], 'guide.md')

    def test_document_payloads(self):
        md = self.v.document('guide.md', self.root)
        self.assertEqual((md['kind'], md['title']), ('markdown', 'Guide'))
        self.assertIn('<h2 id="setup">', md['html'])
        (self.root / 'f.mmd').write_text('flowchart TD\n A-->B\n')
        (self.root / 'g.png').write_bytes(b'\x89PNG')
        self.assertEqual(self.v.document('f.mmd', self.root)['kind'], 'diagram')
        self.assertEqual(self.v.document('g.png', self.root)['url'], '/files/g.png')
        self.assertIsNone(self.v.document('../x.md', self.root))


class DocumentationViewerOverHttp(SimpleTestCase):
    def setUp(self):
        self.v = load_viewer(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'README.md').write_text('# Home\n\nhello\n', encoding='utf-8')
        (self.root / 'pic.png').write_bytes(b'\x89PNG\r\n')
        (self.root / 'x.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        (self.root / 'w.docx').write_bytes(b'PK')
        self.server = self.v.make_server(self.root, '127.0.0.1', 0)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path, headers=None, method='GET'):
        request = urllib.request.Request(self.base + path, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_the_page_and_the_tree(self):
        status, headers, body = self.get('/')
        self.assertEqual(status, 200)
        self.assertIn(b'<title>Documentation</title>', body)
        status, _, body = self.get('/api/tree')
        self.assertEqual([i['path'] for i in json.loads(body)['items']], ['pic.png', 'README.md', 'w.docx', 'x.svg'])

    def test_a_file_added_while_the_server_runs_shows_up(self):
        (self.root / 'new.md').write_text('# New document\n', encoding='utf-8')
        paths = [i['path'] for i in json.loads(self.get('/api/tree')[2])['items']]
        self.assertIn('new.md', paths)
        self.assertEqual(json.loads(self.get('/api/doc?path=new.md')[2])['title'], 'New document')

    def test_documents_files_and_search(self):
        self.assertIn('hello', json.loads(self.get('/api/doc?path=README.md')[2])['html'])
        status, headers, body = self.get('/files/pic.png')
        self.assertEqual((status, headers['Content-Type'], headers['X-Content-Type-Options'], body[:4]), (200, 'image/png', 'nosniff', b'\x89PNG'))
        self.assertEqual(self.get('/files/w.docx')[1]['Content-Disposition'], 'attachment')
        self.assertIn("default-src 'none'", self.get('/files/x.svg')[1]['Content-Security-Policy'])   # an SVG cannot run scripts
        self.assertEqual(json.loads(self.get('/api/search?q=hello')[2])[0]['path'], 'README.md')

    def test_missing_files_and_traversal_are_refused(self):
        self.assertEqual(self.get('/api/doc?path=nope.md')[0], 404)
        self.assertEqual(self.get('/api/doc?path=../README.md')[0], 404)
        self.assertEqual(self.get('/files/%2e%2e/%2e%2e/etc/passwd')[0], 404)
        self.assertEqual(self.get('/files/..%2f..%2fetc%2fpasswd')[0], 404)
        self.assertEqual(self.get('/nothing')[0], 404)

    def test_requests_for_another_host_name_are_refused(self):
        self.assertEqual(self.get('/api/tree', {'Host': 'evil.example.com'})[0], 421)
        self.assertEqual(self.get('/api/tree', {'Host': 'localhost:1'})[0], 200)

    def test_it_is_read_only(self):
        for method in ('POST', 'PUT', 'DELETE'):
            request = urllib.request.Request(self.base + '/api/tree', data=b'x' if method != 'DELETE' else None, method=method)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=10)
            self.assertEqual(caught.exception.code, 405)

    def test_the_page_javascript_is_valid_and_its_flows_run(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is not installed')
        script = re.search(r'<script>([\s\S]*?)</script>', self.v.SHELL).group(1)
        path = Path(self.tmp.name) / 'shell.js'
        path.write_text(script, encoding='utf-8')
        checked = subprocess.run([node, '--check', str(path)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_the_real_documentation_runs_through_the_page_script(self):
        node, smoke = shutil.which('node'), DOCS / 'tools' / 'viewer_smoke.js'
        if not node or not smoke.is_file():
            self.skipTest('Node or tools/viewer_smoke.js is not available')
        real = self.v.make_server(DOCS, '127.0.0.1', 0)
        threading.Thread(target=real.serve_forever, daemon=True).start()
        self.addCleanup(real.server_close)
        self.addCleanup(real.shutdown)
        done = subprocess.run([node, str(smoke), f'http://127.0.0.1:{real.server_address[1]}'], capture_output=True, text=True, timeout=90)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith('OK'), done.stdout)


class SourceIndexIsCurrent(SimpleTestCase):
    def test_the_source_index_lists_every_module(self):
        path = DOCS / 'tools' / 'generate_source_index.py'
        if not path.is_file():
            self.skipTest('docs/tools is not deployed with this copy')
        spec = importlib.util.spec_from_file_location('generate_source_index', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        listed = (DOCS / 'SOURCE_INDEX.md').read_text(encoding='utf-8').splitlines()
        actual = module.render().splitlines()
        extra = sorted(line for line in set(actual) - set(listed) if line.startswith('- `'))      # on disk, not in the index
        gone = sorted(line for line in set(listed) - set(actual) if line.startswith('- `'))       # in the index, not on disk
        hint = ''
        if extra or gone:
            hint = ('\nOn disk but not in the index (a new file, or an OLD one that an archive did not delete: unzipping never deletes files, '
                    'so delete it if it is obsolete):\n  ' + '\n  '.join(extra) + '\nIn the index but not on disk:\n  ' + '\n  '.join(gone))
        self.assertEqual(listed, actual, 'Out of date: run `python docs/tools/generate_source_index.py`' + hint)
