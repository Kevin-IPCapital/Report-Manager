#!/usr/bin/env python3
"""Regenerates the ER diagrams from the real definitions, so they cannot drift.

Reads:  Collector/*/sql/schema.sql (autotask, ncentral, sentinelone, cove) and backend/api/models.py (report_manager).
Writes: docs/09_ER_DIAGRAMS.md and docs/diagrams/er_*.mmd (Mermaid), er_*.dot (Graphviz) and, when Graphviz `dot` is
        installed, er_*.png and er_*.svg.

    python docs/tools/generate_er.py          write everything
    python docs/tools/generate_er.py --check  exit 1 if the Markdown, Mermaid or Graphviz files are out of date

Arrows are only foreign keys DECLARED in the DDL (solid). Links the databases do not enforce (cross-schema IDs, and a few
inside a schema) are drawn dashed and listed as such. Mermaid shows key columns only; the Graphviz images and the tables
list every column."""
import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'docs'
OUT = DOCS / 'diagrams'

SCHEMAS = [  # key, DDL file, display name
    ('autotask', 'Collector/AutotaskLight-main/sql/schema.sql', 'Autotask'),
    ('ncentral', 'Collector/ncentralAPI-main/sql/schema.sql', 'N-central'),
    ('sentinelone', 'Collector/SentinelOne-main/sql/schema.sql', 'SentinelOne'),
    ('cove', 'Collector/Cove-main/sql/schema.sql', 'Cove'),
]

# Links that exist in the data but are not declared as foreign keys: (child, child columns, parent, parent columns, note).
LOGICAL = {
    'autotask': [('customers', 'autotask_company_id', 'company', 'company_id', 'directory entry to company (by ID, not enforced)')],
    'ncentral': [
        ('device', 'org_unit_id', 'customers', 'org_unit_id', 'the device\'s customer'),
        ('device', 'site_id', 'sites', 'site_id', 'the device\'s site (null for devices attached to the customer)'),
        ('device_patch', 'device_id', 'device', 'device_id', 'not a declared FK'),
        ('patch_entry', 'run_id, device_id', 'device_patch', 'run_id, device_id', 'not a declared FK'),
        ('patch_run', 'org_unit_id', 'customers', 'org_unit_id', 'a run covers one customer'),
    ],
    'sentinelone': [('agent', 'site_id', 'customers', 'site_id', 'by site ID, not enforced')],
    'cove': [],
}

NOTES = {
    'autotask': ['Rows are **snapshot-scoped**: `ticket` and `contact` reference `company` by `(snapshot_id, company_id)`, and `ticket_note` references `ticket` by `(snapshot_id, ticket_id)`. Joins must carry the snapshot ID.',
                 '`customers` is the list of companies to load (`autotask_company_id` is the stable ID); it is not snapshot-scoped.',
                 'The `v_*` views return the latest snapshot. There is no assignee column on `ticket` (see [`05_OPERATIONS.md`](05_OPERATIONS.md), Technician data).'],
    'ncentral': ['`patch_entry` references `patch_run` only. There is **no declared foreign key** from `patch_entry` or `device_patch` to `device`, nor from `patch_entry` to `device_patch`; the dashed links show the relationships the queries rely on.',
                 '`device.org_unit_id` is the **customer** org unit: every collector fetches devices per customer (`get_devices_for_org_unit`) and writes that ID there. `device.site_id` is the site, and is null for devices attached directly to the customer.',
                 'A patch run belongs to one customer (`patch_run.org_unit_id`). Usable inventories are per `(run_id, device_id)` in `device_patch` (`error` null, coverage not `unavailable`).'],
    'sentinelone': ['Almost every table references `customer_month` by `(snapshot_id, customer_name)`, which in turn belongs to a `snapshot`. The customer is identified by **name** inside a snapshot; `customers.site_id` is the stable ID and `agent.site_id` carries it.'],
    'cove': ['Every load is a `snapshot`; nothing is updated in place. `customers` is the list of customers to load and is read, never written, by the loader. Devices carry the customer as text, with no ID link to `customers`.',
             'Not yet loaded against a live system.'],
}


# ---------------------------------------------------------------------------------------------- DDL parsing
def strip_comments(sql):
    return re.sub(r'--[^\n]*', '', sql)


def split_top(body):
    items, depth, current = [], 0, ''
    for ch in body:
        depth += ch == '('
        depth -= ch == ')'
        if ch == ',' and depth == 0:
            items.append(current)
            current = ''
        else:
            current += ch
    if current.strip():
        items.append(current)
    return [i.strip() for i in items if i.strip()]


def base_type(text):
    t = re.sub(r'\s+', ' ', text.strip().lower())
    t = re.sub(r'\(.*', '', t).strip()
    mapping = {'double precision': 'double', 'character varying': 'varchar', 'serial': 'integer', 'bigserial': 'bigint',
               'timestamp with time zone': 'timestamptz', 'timestamp without time zone': 'timestamp'}
    return re.sub(r'\W', '', mapping.get(t, t.replace(' ', '_'))) or 'text'


def cols(text):
    return [c.strip() for c in text.split(',') if c.strip()]


def parse_ddl(sql):
    sql = strip_comments(sql)
    tables = {}
    for m in re.finditer(r'CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\(', sql, re.I):
        depth, i = 1, m.end()
        while depth and i < len(sql):
            depth += sql[i] == '('
            depth -= sql[i] == ')'
            i += 1
        table = {'columns': [], 'pk': [], 'fks': [], 'uniques': []}
        for item in split_top(sql[m.end():i - 1]):
            item = re.sub(r'^CONSTRAINT\s+\w+\s+', '', item, flags=re.I)
            up = item.upper()
            if up.startswith('PRIMARY KEY'):
                table['pk'] = cols(re.search(r'\(([^)]*)\)', item).group(1))
            elif up.startswith('FOREIGN KEY'):
                f = re.search(r'FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)\s*\(([^)]*)\)', item, re.I | re.S)
                table['fks'].append((cols(f.group(1)), f.group(2), cols(f.group(3))))
            elif up.startswith('UNIQUE'):
                table['uniques'].append(cols(re.search(r'\(([^)]*)\)', item).group(1)))
            elif up.startswith('CHECK'):
                continue
            else:
                name, rest = re.match(r'(\w+)\s+(.*)', item, re.S).groups()
                kind = re.match(r'[A-Za-z]+(?:\s+(?:PRECISION|VARYING|WITH(?:OUT)?\s+TIME\s+ZONE))*(?:\s*\([^)]*\))?', rest.strip(), re.I)
                column = {'name': name, 'type': base_type(kind.group(0) if kind else 'text'),
                          'notnull': bool(re.search(r'NOT\s+NULL', rest, re.I)) or 'PRIMARY KEY' in rest.upper()}
                table['columns'].append(column)
                if 'PRIMARY KEY' in rest.upper():
                    table['pk'] = [name]
                if re.search(r'\bUNIQUE\b', rest, re.I):
                    table['uniques'].append([name])
                ref = re.search(r'REFERENCES\s+(\w+)\s*\(([^)]*)\)', rest, re.I)
                if ref:
                    table['fks'].append(([name], ref.group(1), cols(ref.group(2))))
        tables[m.group(1)] = table
    for m in re.finditer(r'ALTER TABLE\s+(\w+)\s+ADD COLUMN IF NOT EXISTS\s+(\w+)\s+([A-Za-z ]+?)(?:\s+NOT NULL|\s+DEFAULT|\s*;)', sql, re.I):
        table, name = tables.get(m.group(1)), m.group(2)
        if table and name not in [c['name'] for c in table['columns']]:
            table['columns'].append({'name': name, 'type': base_type(m.group(3)), 'notnull': 'NOT NULL' in m.group(0).upper()})
    views = re.findall(r'CREATE OR REPLACE VIEW\s+(\w+)', sql, re.I)
    return tables, views


def parse_models(path):
    """Django models of the `api` app as tables (the `api_` prefix, `_id` on foreign keys)."""
    kinds = {'CharField': 'varchar', 'EmailField': 'varchar', 'TextField': 'text', 'IntegerField': 'integer', 'BigIntegerField': 'bigint',
             'BooleanField': 'boolean', 'DateTimeField': 'timestamptz', 'UUIDField': 'uuid', 'JSONField': 'jsonb', 'FloatField': 'double'}
    tables = {}
    for node in ast.parse(path.read_text(encoding='utf-8')).body:
        if not (isinstance(node, ast.ClassDef) and any('models.Model' in ast.unparse(b) for b in node.bases)):
            continue
        table = {'columns': [{'name': 'id', 'type': 'bigint', 'notnull': True}], 'pk': ['id'], 'fks': [], 'uniques': [], 'notes': []}
        for st in node.body:
            if isinstance(st, ast.Assign) and isinstance(st.value, ast.Call) and ast.unparse(st.value.func).startswith('models.'):
                kind, kw = ast.unparse(st.value.func).split('.')[-1], {k.arg: ast.unparse(k.value) for k in st.value.keywords}
                name = st.targets[0].id
                if kind == 'ForeignKey':
                    target = ast.unparse(st.value.args[0])
                    parent = 'auth_user' if 'AUTH_USER_MODEL' in target else 'api_' + target.strip("'\"").lower()
                    table['columns'].append({'name': name + '_id', 'type': 'integer' if parent == 'auth_user' else 'bigint', 'notnull': kw.get('null') != 'True'})
                    table['fks'].append(([name + '_id'], parent, ['id']))
                else:
                    table['columns'].append({'name': name, 'type': kinds.get(kind, 'text'), 'notnull': kw.get('null') != 'True'})
                    if kw.get('unique') == 'True':
                        table['uniques'].append([name])
            elif isinstance(st, ast.ClassDef) and st.name == 'Meta':
                for c in ast.walk(st):
                    if isinstance(c, ast.Call) and ast.unparse(c.func).endswith('UniqueConstraint'):
                        kw = {k.arg: k.value for k in c.keywords}
                        fields = [ast.literal_eval(e) for e in kw['fields'].elts]
                        fields = [f + '_id' if f in ('client', 'user') else f for f in fields]
                        cond = ast.unparse(kw['condition']).replace('models.Q', '') if 'condition' in kw else None
                        table['notes'].append(f"unique ({', '.join(fields)})" + (f' where {cond}' if cond else ''))
        tables['api_' + node.name.lower()] = table
    tables['auth_user'] = {'columns': [{'name': 'id', 'type': 'integer', 'notnull': True}, {'name': 'username', 'type': 'varchar', 'notnull': True},
                                       {'name': 'is_staff', 'type': 'boolean', 'notnull': True}], 'pk': ['id'], 'fks': [], 'uniques': [['username']],
                           'notes': ['Django\'s own user table (only its key columns are shown)']}
    return tables, []


def load(key):
    if key == 'report_manager':
        return parse_models(ROOT / 'backend' / 'api' / 'models.py')
    path = next(p for k, p, _ in SCHEMAS if k == key)
    return parse_ddl((ROOT / path).read_text(encoding='utf-8'))


# ---------------------------------------------------------------------------------------------- rendering
def check_logical(key, tables):
    for child, ccols, parent, pcols, _ in LOGICAL.get(key, []):
        for table, columns in ((child, ccols), (parent, pcols)):
            names = {c['name'] for c in tables[table]['columns']}
            missing = [c for c in cols(columns) if c not in names]
            if missing:
                raise SystemExit(f'LOGICAL link in {key} refers to missing column {table}.{missing}: update generate_er.py')


def keyset(table):
    keys = set(table['pk']) | {c for fk in table['fks'] for c in fk[0]}
    return [c for c in table['columns'] if c['name'] in keys]


def relationships(key, tables):
    """(parent, child, columns text, declared, optional) for declared FKs, then the logical ones."""
    rows = []
    for child, table in tables.items():
        for fcols, parent, _pcols in table['fks']:
            nullable = any(not next(c for c in table['columns'] if c['name'] == f)['notnull'] for f in fcols)
            rows.append((parent, child, ', '.join(fcols), True, nullable))
    for child, ccols, parent, _pcols, note in LOGICAL.get(key, []):
        rows.append((parent, child, f'{ccols}: {note}', False, True))
    return rows


def mermaid(key, tables):
    lines = ['erDiagram']
    for name, table in tables.items():
        lines.append(f'  {name} {{')
        for c in keyset(table):
            tags = [t for t, on in (('PK', c['name'] in table['pk']), ('FK', any(c['name'] in fk[0] for fk in table['fks']))) if on]
            lines.append(f"    {c['type']} {c['name']}" + (f" {', '.join(tags)}" if tags else ''))
        lines.append('  }')
    for parent, child, text, declared, nullable in relationships(key, tables):
        left = '|o' if nullable else '||'
        link = '--' if declared else '..'
        label = text.replace('"', "'")
        lines.append(f'  {parent} {left}{link}o{{ {child} : "{label}"')
    return '\n'.join(lines) + '\n'


def dot(key, tables, title):
    esc = lambda s: s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    out = ['digraph ER {', f'  graph [rankdir=LR, bgcolor="white", nodesep=.35, ranksep=.9, pad=.3, fontname="DejaVu Sans", label=<<B>{esc(title)}</B>>, labelloc=t, fontsize=16];',
           '  node [shape=plain, fontname="DejaVu Sans", fontsize=10];', '  edge [color="#697896", fontname="DejaVu Sans", fontsize=8, arrowsize=.7];']
    for name, table in tables.items():
        fk_cols = {c for fk in table['fks'] for c in fk[0]}
        rows = [f'<TR><TD COLSPAN="2" BGCOLOR="#2B3D89"><FONT COLOR="white"><B>{esc(name)}</B></FONT></TD></TR>']
        for c in table['columns']:
            tag = ' '.join(t for t, on in (('PK', c['name'] in table['pk']), ('FK', c['name'] in fk_cols)) if on)
            bg = ' BGCOLOR="#EEF1FB"' if tag else ''
            label = f'<B>{esc(c["name"])}</B>' if c['name'] in table['pk'] else esc(c['name'])
            tag_html = f' <FONT COLOR="#2B3D89" POINT-SIZE="8">{tag}</FONT>' if tag else ''   # Graphviz rejects an empty FONT element
            rows.append(f'<TR><TD ALIGN="LEFT"{bg}>{label}{tag_html}</TD><TD ALIGN="LEFT"{bg}><FONT COLOR="#697896">{esc(c["type"])}</FONT></TD></TR>')
        out.append(f'  {name} [label=<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4" COLOR="#2B3D89">' + ''.join(rows) + '</TABLE>>];')
    for parent, child, text, declared, _ in relationships(key, tables):
        style = 'solid' if declared else 'dashed'
        out.append(f'  {parent} -> {child} [label="{text.split(":")[0]}", style={style}, arrowhead=crow, arrowtail=none];')
    return '\n'.join(out) + '\n}\n'


CROSS = '''erDiagram
  api_client ||--o{ api_clientsourcemap : "linked by"
  api_client ||--o{ api_clientalias : "known as"
  api_client |o--o{ api_patchsnapshot : "scored in"
  api_clientsourcemap {
    varchar source "autotask, ncentral, sentinelone, cove"
    varchar external_id "the source system's own ID, as text"
  }
  autotask_ticket {
    bigint ticket_id PK
    bigint company_id
  }
  autotask_customers {
    bigint autotask_company_id
  }
  ncentral_device {
    bigint device_id PK
    bigint org_unit_id "customer"
    bigint site_id "site"
  }
  ncentral_customers {
    bigint org_unit_id PK
  }
  sentinelone_agent {
    text agent_id PK
    text site_id
  }
  sentinelone_customers {
    text site_id
  }
  cove_customers {
    integer id PK
  }
  autotask_ticket }o..o| api_clientsourcemap : "company_id = external_id (autotask)"
  autotask_customers }o..o| api_clientsourcemap : "autotask_company_id = external_id (autotask)"
  ncentral_device }o..o| api_clientsourcemap : "org_unit_id = external_id (ncentral)"
  ncentral_customers }o..o| api_clientsourcemap : "org_unit_id = external_id (ncentral)"
  sentinelone_agent }o..o| api_clientsourcemap : "site_id = external_id (sentinelone)"
  sentinelone_customers }o..o| api_clientsourcemap : "site_id = external_id (sentinelone)"
  cove_customers }o..o| api_clientsourcemap : "id = external_id (cove)"
'''
CROSS_CHECK = [('autotask', 'ticket', 'company_id'), ('autotask', 'customers', 'autotask_company_id'), ('ncentral', 'device', 'org_unit_id'),
               ('ncentral', 'device', 'site_id'), ('ncentral', 'customers', 'org_unit_id'), ('sentinelone', 'agent', 'site_id'),
               ('sentinelone', 'customers', 'site_id'), ('cove', 'customers', 'id'), ('report_manager', 'api_clientsourcemap', 'external_id'),
               ('report_manager', 'api_clientsourcemap', 'source')]

CLIENT_MASTER = '''erDiagram
  api_client ||--|| client_master : "one row per client"
  client_master {
    bigint client_id PK
    varchar client_name
    bigint_array autotask_company_ids
    bigint_array ncentral_org_unit_ids
    varchar_array sentinelone_site_ids
    varchar_array cove_customer_ids
  }
  client_master ||..o{ autotask_ticket : "company_id = ANY (autotask_company_ids)"
  client_master ||..o{ ncentral_device : "org_unit_id = ANY (ncentral_org_unit_ids)"
  client_master ||..o{ sentinelone_agent : "site_id = ANY (sentinelone_site_ids)"
  client_master ||..o{ cove_customers : "id = ANY (cove_customer_ids)"
'''


def summary_table(tables, views):
    rows = ['| Table | Primary key | Declared foreign keys | Columns |', '|---|---|---|---|']
    for name, t in tables.items():
        fks = '<br>'.join(f"({', '.join(f[0])}) → `{f[1]}`" for f in t['fks']) or '—'
        columns = ', '.join(f"`{c['name']}`" for c in t['columns'])
        note = ''.join(f' ({n})' for n in t.get('notes', []))
        rows.append(f"| `{name}` | {', '.join(t['pk']) or '—'} | {fks} | {len(t['columns'])}: {columns}{note} |")
    text = '\n'.join(rows)
    if views:
        text += '\n\nViews: ' + ', '.join(f'`{v}`' for v in views) + '.'
    return text


def render_all():
    """{relative path: text} for every file that does not need Graphviz to build."""
    files, sections = {}, []
    parsed = {key: load(key) for key, _, _ in SCHEMAS}
    parsed['report_manager'] = load('report_manager')
    for key, (tables, _) in parsed.items():
        check_logical(key, tables)
    for key, table, column in CROSS_CHECK:
        if column not in {c['name'] for c in parsed[key][0][table]['columns']}:
            raise SystemExit(f'Cross-schema diagram refers to missing column {key}.{table}.{column}')
    names = {k: n for k, _, n in SCHEMAS} | {'report_manager': 'Application (report_manager)'}
    for key, (tables, views) in parsed.items():
        files[f'diagrams/er_{key}.mmd'] = mermaid(key, tables)
        files[f'diagrams/er_{key}.dot'] = dot(key, tables, f'{names[key]} schema')
        source = 'Django models (`backend/api/models.py`) plus Django\'s `auth_user`' if key == 'report_manager' else f"`{dict((k, p) for k, p, _ in SCHEMAS)[key]}`"
        notes = NOTES.get(key, [
            'These tables are created by `python manage.py migrate`. Django also owns `auth_*` and `django_*` tables, not shown.',
            '`api_clientsourcemap` links a client to a customer in another schema by **ID text**, not a database foreign key (see the cross-schema diagram).',
            '`api_patchsnapshot` keeps `client_id` null for the all-devices snapshot; its `weights`, `components` and `scores` are JSON.'])
        sections.append(f'''## {names[key]}

Source: {source}. {len(tables)} table(s){f", {len(views)} view(s)" if views else ""}. Solid arrows are foreign keys declared in the definition; dashed arrows are links that exist in the data but are not enforced.

```mermaid
{mermaid(key, tables)}```

Complete column list with types: ![{names[key]} ER](diagrams/er_{key}.png) ([SVG](diagrams/er_{key}.svg)).

{summary_table(tables, views)}

{chr(10).join('- ' + n for n in notes)}
''')
    files['diagrams/er_cross_schema.mmd'] = CROSS
    files['diagrams/er_client_master.mmd'] = CLIENT_MASTER
    md = f'''# 9. Entity-relationship diagrams

Generated from the real definitions by `docs/tools/generate_er.py`: the collectors' `sql/schema.sql` files and the Django models. `backend/api/test_docs.py` fails if these files are out of date, so run `python docs/tools/generate_er.py` after changing a schema or a model.

**Reading them.** The Mermaid diagrams here show **key columns only** (primary and foreign keys), so they stay readable; the images and the tables list every column. A solid line is a foreign key the database enforces. A **dashed line is a link that exists in the data but is not enforced**: for example, N-central `patch_entry` has no foreign key to `device`, and every cross-schema link is by ID.

Contents: {', '.join(f"[{names[k]}](#{re.sub(r'[^a-z0-9]+', '-', names[k].lower()).strip('-')})" for k in parsed)}, [across schemas](#across-schemas), [the master client list](#the-joinable-master-client-list).

{chr(10).join(sections)}
## Across schemas

No database foreign key crosses a schema. The master client (`api_client`) ties the schemas together through `api_clientsourcemap`, whose `external_id` holds each system's own ID as text: Autotask `company_id`, N-central `org_unit_id` (the customer), SentinelOne `site_id`, Cove `customers.id`. Names are never a join key.

```mermaid
{CROSS}```

A join from a source table to its client therefore casts the ID to text:

```sql
SELECT c.name, d.device_name
FROM ncentral.device d
JOIN report_manager.api_clientsourcemap m ON m.source = 'ncentral' AND m.external_id = d.org_unit_id::text
JOIN report_manager.api_client c ON c.id = m.client_id;
```

## The joinable master client list

A **view** (not a table), `client_master`, with one row per client and an array of references per schema, so that any source table can be joined to the client in one step without going through the link table. Arrays, because one client can have several N-central org units. It is created on request with `python manage.py client_master_view` (PostgreSQL only; `--sql` prints it, `--drop` removes it) and is built from the link table, so it is always current. DATA-03 in [`06_STATUS_AND_BACKLOG.md`](06_STATUS_AND_BACKLOG.md).

```mermaid
{CLIENT_MASTER}```
'''
    files['09_ER_DIAGRAMS.md'] = md
    return files


def main():
    files = render_all()
    if '--check' in sys.argv:
        stale = [p for p, text in files.items() if not (DOCS / p).is_file() or (DOCS / p).read_text(encoding='utf-8') != text]
        print('up to date' if not stale else 'STALE: ' + ', '.join(stale))
        sys.exit(1 if stale else 0)
    OUT.mkdir(exist_ok=True)
    for path, text in files.items():
        (DOCS / path).write_text(text, encoding='utf-8')
    if shutil.which('dot'):
        for path in sorted(OUT.glob('er_*.dot')):
            for fmt, args in (('png', ['-Gdpi=80']), ('svg', [])):
                subprocess.run(['dot', f'-T{fmt}', *args, str(path), '-o', str(path.with_suffix('.' + fmt))], check=True)
    else:
        print('Graphviz `dot` not found: PNG and SVG images were not rebuilt')
    print(f'wrote {len(files)} files')


if __name__ == '__main__':
    main()
