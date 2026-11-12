"""The collectors' schema files, and the DATA-01 change to them (imported_at on every table). These read the SQL files as text: nothing here can
run SQL, so what they pin down is everything that can be known without a database: that every table is covered, that the statements are the
safe forms and nothing else, that they sit where they cannot break a view, and that the loaders do not depend on the column list."""
import importlib.util
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR).parent
COLLECTORS = ROOT / 'Collector'
GENERATOR = ROOT / 'docs' / 'tools' / 'generate_imported_at.py'
FOLDERS = ['AutotaskLight-main', 'ncentralAPI-main', 'SentinelOne-main', 'Cove-main']
SAFE = (re.compile(r'^ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;$'), re.compile(r'^ALTER TABLE (\w+) ALTER COLUMN imported_at SET DEFAULT now\(\);$'))


def schema(folder):
    return (COLLECTORS / folder / 'sql' / 'schema.sql').read_text(encoding='utf-8')


def block_of(sql):
    start = sql.index('-- BEGIN imported_at')
    return sql[start:sql.index('-- END imported_at') + len('-- END imported_at')]


def view_problems(sql):
    """What would break a view when a column is added to a table under it. See the test that uses this."""
    sql = re.sub(r'^\s*--.*$', '', sql, flags=re.M)
    dropped = set(re.findall(r'DROP VIEW IF EXISTS\s+(\w+)', sql, re.I))
    problems = []
    for name, select, rest in re.findall(r'CREATE OR REPLACE VIEW\s+(\w+)\s+AS\s+SELECT\s+(.*?)\s+(FROM\b.*?);', sql, re.S | re.I):
        items = ImportedAtInTheSchemas.items(select)
        stars = [i for i in items if re.fullmatch(r'(\w+\.)?\*', i)]           # `t.*` or `*` as a select item; count(*) is not one
        if name not in dropped and stars and len(items) > 1:
            problems.append(f'view {name} is replaced in place and has a star among other columns')
        if '*' in stars and re.search(r'\bJOIN\b', rest, re.I):
            problems.append(f'view {name} selects * over a join, which now lists imported_at twice')
    return problems


class ImportedAtInTheSchemas(SimpleTestCase):
    def setUp(self):
        if not COLLECTORS.is_dir():
            self.skipTest('the collectors are not deployed with this copy')

    def test_every_table_of_every_schema_gets_the_column_and_its_default(self):
        for folder in FOLDERS:
            sql = schema(folder)
            tables = re.findall(r'^\s*CREATE TABLE IF NOT EXISTS\s+(\w+)', sql, re.M)
            self.assertGreater(len(tables), 5, folder)
            statements = [line for line in block_of(sql).splitlines() if not line.startswith('--')]
            added = [SAFE[0].match(line).group(1) for line in statements if SAFE[0].match(line)]
            defaulted = [SAFE[1].match(line).group(1) for line in statements if SAFE[1].match(line)]
            self.assertEqual(added, tables, f'{folder}: a table has no imported_at (run docs/tools/generate_imported_at.py)')
            self.assertEqual(defaulted, tables, folder)

    def test_the_block_holds_nothing_but_those_two_safe_statements_per_table(self):
        for folder in FOLDERS:
            for line in block_of(schema(folder)).splitlines():
                if line.startswith('--') or not line.strip():
                    continue
                self.assertTrue(any(p.match(line) for p in SAFE), f'{folder}: unexpected statement: {line}')

    def test_nothing_in_the_block_can_change_or_remove_data(self):
        for folder in FOLDERS:
            text = re.sub(r'^--.*$', '', block_of(schema(folder)), flags=re.M).upper()
            for word in ('UPDATE', 'DELETE', 'DROP', 'TRUNCATE', 'INSERT', 'NOT NULL', 'TYPE ', 'RENAME'):
                self.assertNotIn(word, text, f'{folder}: {word}')
        # existing rows keep NULL: the column is added without a default, and the default is set separately, for new rows only
        self.assertNotIn('ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ DEFAULT', schema('Cove-main'))

    def test_the_column_is_not_defined_anywhere_else_so_adding_it_cannot_clash(self):
        for folder in FOLDERS:
            sql = schema(folder)
            outside = sql[:sql.index('-- BEGIN imported_at')] + sql[sql.index('-- END imported_at'):]
            self.assertNotIn('imported_at', outside.replace('-- END imported_at', ''), folder)

    def test_the_block_is_after_the_last_table_and_before_the_first_view(self):
        for folder in FOLDERS:
            sql = schema(folder)
            last_table = max(m.start() for m in re.finditer(r'^\s*CREATE TABLE IF NOT EXISTS', sql, re.M))
            first_view = min(m.start() for m in re.finditer(r'^\s*(CREATE OR REPLACE VIEW|CREATE VIEW|DROP VIEW)', sql, re.M))
            self.assertLess(last_table, sql.index('-- BEGIN imported_at'), folder)
            self.assertLess(sql.index('-- END imported_at'), first_view, folder)

    def test_a_view_replaced_in_place_never_has_a_star_among_other_columns_and_none_has_a_bare_star_over_a_join(self):
        """Two ways an added column can break a view. (1) CREATE OR REPLACE VIEW may add columns at the END of a view but not in the middle, so a
        view that is replaced in place (not dropped first) must have the star as its whole select list. (2) A bare `SELECT *` over a join lists
        every joined table's columns, so a column that two tables now both have (imported_at) would be in the view twice, which is an error."""
        problems = [f'{folder}: {problem}' for folder in FOLDERS for problem in view_problems(schema(folder))]
        self.assertEqual(problems, [])

    @staticmethod
    def items(select):
        """The items of a select list, split on its top-level commas (a leading DISTINCT ON (...) is not an item)."""
        items, depth, current = [], 0, ''
        for ch in re.sub(r'^DISTINCT ON \([^)]*\)\s*', '', ' '.join(select.split()), flags=re.I):
            depth += ch == '('
            depth -= ch == ')'
            if ch == ',' and depth == 0:
                items.append(current.strip())
                current = ''
            else:
                current += ch
        return items + [current.strip()]

    def test_the_standalone_migration_files_and_the_blocks_are_what_the_tool_makes(self):
        spec = importlib.util.spec_from_file_location('generate_imported_at', GENERATOR)
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        stale = [p for p, text in tool.render_all().items() if (ROOT / p).read_text(encoding='utf-8') != text]
        self.assertEqual(stale, [], 'out of date: run `python docs/tools/generate_imported_at.py`')

    def test_the_standalone_migration_is_one_transaction_in_the_right_schema(self):
        for folder, name in zip(FOLDERS, ('autotask', 'ncentral', 'sentinelone', 'cove')):
            text = (COLLECTORS / folder / 'sql' / 'migrations' / '2026-11-12_imported_at.sql').read_text(encoding='utf-8')
            self.assertIn('BEGIN;', text)
            self.assertIn(f'SET LOCAL search_path TO {name}, public;', text)
            self.assertTrue(text.rstrip().endswith('COMMIT;'))
            self.assertEqual(block_of(text), block_of(schema(folder)))                 # the same statements as in schema.sql

    def test_running_the_block_twice_changes_nothing_because_every_statement_is_repeatable(self):
        for folder in FOLDERS:
            for line in block_of(schema(folder)).splitlines():
                if line.startswith('ALTER'):
                    self.assertTrue('IF NOT EXISTS' in line or 'SET DEFAULT' in line, line)   # adding is guarded, and setting a default again is harmless

    def test_the_loaders_name_their_columns_so_a_new_column_cannot_misplace_their_values(self):
        """The loaders say INSERT INTO <table> (<columns>) ..., so a column added to the table takes its default and nothing shifts. A COPY with no
        column list, or an INSERT with no column list, would be affected by the added column."""
        problems = []
        for path in (p for p in COLLECTORS.rglob('*.py') if 'tests' not in p.parts and not p.name.startswith('test_')):   # the loaders, not their tests
            code = path.read_text(encoding='utf-8')
            for match in re.finditer(r'INSERT INTO\s+\S+\s*(\S)', code):
                if match.group(1) != '(':
                    problems.append(f'{path.name}: INSERT with no column list near "{code[match.start():match.start() + 50]!r}"')
            if re.search(r'\bCOPY\s+\w', code) and 'IMMUTABLE COPY' not in code:
                problems.append(f'{path.name}: uses COPY')
        self.assertEqual(problems, [])


class TheViewRule(SimpleTestCase):
    """The select-list splitter behind the view rule, shown to see what it should."""
    def items(self, select):
        return ImportedAtInTheSchemas.items(select)

    def test_a_star_among_other_columns_is_found(self):
        items = self.items('t.*, extra')
        self.assertEqual(items, ['t.*', 'extra'])

    def test_a_lone_star_and_a_lone_star_after_distinct_on_are_one_item(self):
        for select in ('t.*', '*', 'DISTINCT ON (company_filter) *'):
            self.assertEqual(len(self.items(select)), 1, select)

    def test_count_star_inside_an_aggregate_is_not_a_star_column(self):
        items = self.items('company_name, count(*) AS total, count(*) FILTER (WHERE x) AS y')
        self.assertEqual(len(items), 3)
        self.assertFalse(any(re.fullmatch(r'(\w+\.)?\*', i) for i in items))

    def test_commas_inside_brackets_do_not_split_an_item(self):
        self.assertEqual(self.items("round(avg(a), 2) AS x, coalesce(b, 0)"), ['round(avg(a), 2) AS x', 'coalesce(b, 0)'])


class TheViewRuleOnExamples(SimpleTestCase):
    """view_problems, shown to find what it should and to leave alone what it should."""

    def test_a_star_among_other_columns_in_a_view_replaced_in_place_is_a_problem(self):
        sql = 'CREATE OR REPLACE VIEW v AS\nSELECT t.*, extra\nFROM t;'
        self.assertEqual(view_problems(sql), ['view v is replaced in place and has a star among other columns'])

    def test_the_same_view_dropped_first_is_fine_because_it_is_made_afresh(self):
        sql = 'DROP VIEW IF EXISTS v CASCADE;\nCREATE OR REPLACE VIEW v AS\nSELECT t.*, extra\nFROM t;'
        self.assertEqual(view_problems(sql), [])

    def test_a_bare_star_over_a_join_is_a_problem_even_if_dropped_first(self):
        sql = 'DROP VIEW IF EXISTS v;\nCREATE OR REPLACE VIEW v AS\nSELECT *\nFROM a JOIN b USING (id);'
        self.assertEqual(view_problems(sql), ['view v selects * over a join, which now lists imported_at twice'])

    def test_a_table_star_over_a_join_and_a_lone_star_are_fine(self):
        self.assertEqual(view_problems('CREATE OR REPLACE VIEW v AS\nSELECT c.*\nFROM c\nJOIN s USING (id);'), [])
        self.assertEqual(view_problems('CREATE OR REPLACE VIEW v AS\nSELECT DISTINCT ON (k) *\nFROM snapshot\nORDER BY k;'), [])

    def test_count_star_and_commented_out_views_are_not_problems(self):
        self.assertEqual(view_problems('CREATE OR REPLACE VIEW v AS\nSELECT name, count(*) AS n\nFROM t JOIN u USING (id)\nGROUP BY name;'), [])
        self.assertEqual(view_problems('-- CREATE OR REPLACE VIEW v AS SELECT t.*, x FROM t;\n'), [])
