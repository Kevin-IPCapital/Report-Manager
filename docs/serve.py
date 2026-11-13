#!/usr/bin/env python3
"""Documentation viewer: every file in this folder, in one page.

    python docs/serve.py                  then open http://localhost:8765  (it opens itself)
    python docs/serve.py --no-browser     do not open a browser
    python docs/serve.py --port 9000

It reads the folder on every request, so a file you add, rename or edit shows up on its own (the page also checks every few
seconds and reloads the open document when it changes). It shows Markdown (links between documents work, tables, code),
Mermaid diagrams (.mmd, and the ```mermaid blocks in Markdown), images, PDFs, SQL and text; Word files are offered as downloads.
There is full-text search across all of it.

Why a small server and not a plain HTML file: a page opened from disk is not allowed to list a folder or read its neighbours.
This needs only Python (no packages) and listens on this computer only. Diagrams are drawn with Mermaid, loaded from
docs/vendor/mermaid.min.js if you put a copy there (for offline use), otherwise from the internet; without either, the diagram
source is shown instead."""
import argparse
import hashlib
import html
import json
import mimetypes
import posixpath
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent
TREE_HIDDEN = {'__pycache__', 'node_modules', '.git', 'vendor'}   # not listed (vendor/ is still served, for Mermaid)
FILE_HIDDEN = {'__pycache__', 'node_modules', '.git'}              # never served
VIEWER_FILES = {'serve.py', 'Open-Docs.cmd'}                       # the viewer itself is not documentation
KINDS = {'.md': 'markdown', '.mmd': 'diagram', '.png': 'image', '.svg': 'image', '.jpg': 'image', '.jpeg': 'image', '.gif': 'image',
         '.pdf': 'pdf', '.docx': 'download', '.xlsx': 'download', '.pptx': 'download', '.zip': 'download'}
TEXT_EXT = {'.sql', '.dot', '.txt', '.json', '.py', '.js', '.ps1', '.cmd', '.yml', '.yaml', '.csv', '.log', '.ini'}
SEARCH_EXT = {'.md', '.mmd'} | TEXT_EXT
MAX_TEXT = 2 * 1024 * 1024
MERMAID_CDN = 'https://cdn.jsdelivr.net/npm/mermaid@10.9.3/dist/mermaid.min.js'


# ------------------------------------------------------------------------------------------------------ files
def kind_of(path):
    ext = Path(path).suffix.lower()
    return KINDS.get(ext) or ('text' if ext in TEXT_EXT else 'other')


def natural_key(text):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)', text)]


def hidden(parts, names):
    return any(p.startswith('.') or p in names for p in parts)


def resolve(rel, root=ROOT):
    """A file inside the docs folder, or None. Refuses anything that escapes the folder (../, absolute paths, links)."""
    rel = unquote(rel).replace('\\', '/').lstrip('/')
    if not rel or '\x00' in rel:
        return None
    target = (root / rel).resolve()
    try:
        parts = target.relative_to(root.resolve()).parts
    except ValueError:
        return None
    return target if target.is_file() and not hidden(parts, FILE_HIDDEN) else None


def front_matter(text):
    m = re.match(r'---\s*\n(.*?)\n---\s*\n', text, re.S)
    return (m.group(1), text[m.end():]) if m else ('', text)


def doc_title(path, text=None):
    if kind_of(path) != 'markdown':
        return Path(path).name
    if text is None:
        text = path.read_text(encoding='utf-8', errors='replace')
    meta, body = front_matter(text)
    m = re.search(r'^title:\s*"?(.*?)"?\s*$', meta, re.M)
    if m and m.group(1):
        return m.group(1)
    in_fence = False
    for line in body.splitlines():
        if re.match(r'^\s*(```|~~~)', line):
            in_fence = not in_fence
        elif not in_fence and (h := re.match(r'^#\s+(.+?)\s*#*\s*$', line)):
            return strip_inline(h.group(1))
    return path.stem.replace('_', ' ')


def scan(root=ROOT):
    """Every viewable file under root, re-read each time so new files appear without restarting."""
    items = []
    for p in sorted(root.rglob('*'), key=lambda q: natural_key(q.relative_to(root).as_posix())):
        rel = p.relative_to(root)
        if not p.is_file() or hidden(rel.parts[:-1], TREE_HIDDEN) or rel.name.startswith('.') or (len(rel.parts) == 1 and rel.name in VIEWER_FILES):
            continue
        stat = p.stat()
        items.append({'path': rel.as_posix(), 'kind': kind_of(rel.name), 'title': doc_title(p) if p.suffix.lower() == '.md' else rel.name,
                      'size': stat.st_size, 'modified': int(stat.st_mtime_ns // 1_000_000)})
    return items


def signature(items):
    return hashlib.sha1('\n'.join(f"{i['path']}|{i['modified']}|{i['size']}" for i in items).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------ Markdown
FENCE = re.compile(r'^(\s*)(`{3,}|~{3,})\s*([\w+.-]*)')
HEADING = re.compile(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$')
RULE = re.compile(r'^\s{0,3}([-*_])(\s*\1){2,}\s*$')
LIST = re.compile(r'^(\s*)([-*+]|\d+[.)])\s+(.*)$')
TABLE_SEP = re.compile(r'^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$')
STASH = '\ue000{}\ue001'


def strip_inline(text):
    text = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', text)
    return re.sub(r'[`*]', '', text).strip()


def slugify(text, used):
    base = re.sub(r'[^a-z0-9]+', '-', strip_inline(text).lower()).strip('-') or 'section'
    slug, n = base, 2
    while slug in used:
        slug, n = f'{base}-{n}', n + 1
    used.add(slug)
    return slug


class Context:
    """Per-document state: where it lives (to resolve relative links), headings, and every link seen (for the link check)."""

    def __init__(self, doc_path, exists):
        self.doc_path, self.exists = doc_path, exists
        self.used, self.toc, self.links = set(), [], []

    def link(self, url):
        """(href, is_external, exists) for a link target."""
        url = html.unescape(url)
        if url.startswith('#'):
            return f'#/{self.doc_path}{url}', False, True
        scheme = re.match(r'^([a-z][a-z0-9+.-]*):', url, re.I)
        if scheme:
            return (url, True, True) if scheme.group(1).lower() in ('http', 'https', 'mailto') else ('#', False, False)
        path, _, fragment = url.partition('#')
        target = posixpath.normpath(posixpath.join(posixpath.dirname(self.doc_path), unquote(path)))
        found = not target.startswith('..') and self.exists(target)
        self.links.append({'from': self.doc_path, 'url': url, 'target': target, 'exists': found})
        return f'#/{quote(target)}' + (f'#{fragment}' if fragment else ''), False, found


def inline(text, ctx):
    stash = []

    def keep(m):
        stash.append(f'<code>{html.escape(m.group(2).strip(), quote=False)}</code>')
        return STASH.format(len(stash) - 1)

    text = re.sub(r'(`+)(.+?)\1', keep, text)
    text = html.escape(text, quote=False)
    text = re.sub(r'&lt;br\s*/?&gt;', '<br>', text)

    def image(m):
        alt, url = m.group(1), m.group(2)
        if re.match(r'^[a-z][a-z0-9+.-]*:', html.unescape(url), re.I):
            src = url if re.match(r'^https?:', html.unescape(url), re.I) else ''
        else:
            _, _, found = ctx.link(url)
            src = '/files/' + quote(posixpath.normpath(posixpath.join(posixpath.dirname(ctx.doc_path), unquote(html.unescape(url)))))
            if not found:
                return f'<span class="broken" title="File not found">[image missing: {html.escape(alt)}]</span>'
        return f'<img src="{html.escape(src)}" alt="{html.escape(alt)}" loading="lazy">'

    def link(m):
        label, url = m.group(1), m.group(2)
        href, external, found = ctx.link(url)
        extra = ' target="_blank" rel="noopener noreferrer"' if external else ('' if found else ' class="broken" title="File not found"')
        return f'<a href="{html.escape(href)}"{extra}>{label}</a>'

    text = re.sub(r'!\[([^\]]*)\]\(([^)\s]+)\)(?:\{[^}]*\})?', image, text)
    text = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', link, text)
    text = re.sub(r'\*\*(?=\S)(.+?)(?<=\S)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])', r'<em>\1</em>', text)
    return re.sub('\ue000(\\d+)\ue001', lambda m: stash[int(m.group(1))], text)


def split_row(line):
    """Cells of a table row; a pipe inside backticks or written as \\| belongs to the cell."""
    line = line.strip()
    if line.startswith('|'):
        line = line[1:]
    if line.endswith('|') and not line.endswith('\\|'):
        line = line[:-1]
    cells, current, in_code, i = [], '', False, 0
    while i < len(line):
        ch = line[i]
        if ch == '\\' and i + 1 < len(line) and line[i + 1] == '|':
            current, i = current + '|', i + 2
            continue
        if ch == '`':
            in_code = not in_code
        if ch == '|' and not in_code:
            cells.append(current.strip())
            current = ''
        else:
            current += ch
        i += 1
    cells.append(current.strip())
    return cells


def block_start(lines, i):
    line = lines[i]
    return bool(FENCE.match(line) or HEADING.match(line) or RULE.match(line) or line.lstrip().startswith('>') or LIST.match(line)
                or ('|' in line and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]) and '-' in lines[i + 1]))


def blocks(lines, ctx):
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if m := FENCE.match(line):
            indent, fence, lang = len(m.group(1)), m.group(2), m.group(3).lower()
            body, i = [], i + 1
            while i < n and not re.match(rf'^\s*{re.escape(fence[0])}{{{len(fence)},}}\s*$', lines[i]):
                body.append(lines[i][indent:] if lines[i][:indent].strip() == '' else lines[i])
                i += 1
            i += 1
            code = html.escape('\n'.join(body), quote=False)
            if lang == 'mermaid':
                out.append(f'<pre class="mermaid">{code}</pre>')
            else:
                label = f' data-lang="{html.escape(lang)}"' if lang else ''
                out.append(f'<pre class="code"{label}><code>{code}</code></pre>')
            continue
        if m := HEADING.match(line):
            level, text = len(m.group(1)), m.group(2)
            slug = slugify(text, ctx.used)
            ctx.toc.append({'level': level, 'text': strip_inline(text), 'id': slug})
            out.append(f'<h{level} id="{slug}">{inline(text, ctx)}</h{level}>')
            i += 1
            continue
        if RULE.match(line):
            out.append('<hr>')
            i += 1
            continue
        if line.lstrip().startswith('>'):
            quoted = []
            while i < n and lines[i].lstrip().startswith('>'):
                quoted.append(re.sub(r'^\s*>\s?', '', lines[i]))
                i += 1
            out.append(f'<blockquote>{blocks(quoted, ctx)}</blockquote>')
            continue
        if '|' in line and i + 1 < n and TABLE_SEP.match(lines[i + 1]) and '-' in lines[i + 1]:
            head, seps = split_row(line), split_row(lines[i + 1])
            align = ['center' if s.startswith(':') and s.endswith(':') else 'right' if s.endswith(':') else 'left' if s.startswith(':') else '' for s in seps]
            i += 2
            rows = []
            while i < n and lines[i].strip() and '|' in lines[i]:
                rows.append(split_row(lines[i]))
                i += 1
            def cell(tag, text, k):
                style = f' style="text-align:{align[k]}"' if k < len(align) and align[k] else ''
                return f'<{tag}{style}>{inline(text, ctx)}</{tag}>'

            width = len(head)
            body = ''.join('<tr>' + ''.join(cell('td', (r + [''] * width)[k], k) for k in range(width)) + '</tr>' for r in rows)
            out.append('<div class="table-scroll"><table><thead><tr>' + ''.join(cell('th', h, k) for k, h in enumerate(head)) + f'</tr></thead><tbody>{body}</tbody></table></div>')
            continue
        if LIST.match(line):
            html_list, i = parse_list(lines, i, ctx)
            out.append(html_list)
            continue
        para = [line.strip()]
        i += 1
        while i < n and lines[i].strip() and not block_start(lines, i):
            para.append(lines[i].strip())
            i += 1
        out.append(f'<p>{inline(" ".join(para), ctx)}</p>')
    return ''.join(out)


def indent_of(line):
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())


def parse_list(lines, i, ctx):
    n, first = len(lines), LIST.match(lines[i])
    base, ordered = indent_of(lines[i]), first.group(2)[0].isdigit()
    start = int(re.match(r'\d+', first.group(2)).group()) if ordered else 1
    items = []
    while i < n:
        line = lines[i]
        if not line.strip():
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            nxt = LIST.match(lines[j]) if j < n else None
            if j < n and (indent_of(lines[j]) > base or (nxt and indent_of(lines[j]) == base and nxt.group(2)[0].isdigit() == ordered)):
                if items:
                    items[-1].append('')
                i = j
                continue
            break
        m, ind = LIST.match(line), indent_of(line)
        if m and ind == base and m.group(2)[0].isdigit() == ordered:
            items.append([m.group(3)])
        elif m and ind <= base:
            break
        elif ind > base and items:
            items[-1].append(line)
        else:
            break
        i += 1
    rendered = []
    for item in items:
        rest = item[1:]
        pad = min((indent_of(x) for x in rest if x.strip()), default=0)
        text = blocks([item[0]] + [x.expandtabs(4)[pad:] if x.strip() else '' for x in rest], ctx)
        rendered.append('<li>' + re.sub(r'^<p>(.*?)</p>', r'\1', text, count=1, flags=re.S) + '</li>')
    tag = 'ol' if ordered else 'ul'
    return f'<{tag}{f" start={start}" if ordered and start != 1 else ""}>{"".join(rendered)}</{tag}>', i


def render_markdown(text, doc_path='', exists=lambda path: True):
    """(html, headings, links) for Markdown text. Only HTML this function writes is produced: everything typed is escaped."""
    _meta, body = front_matter(text)
    ctx = Context(doc_path, exists)
    return blocks(body.splitlines(), ctx), ctx.toc, ctx.links


def check_links(root=ROOT):
    """Relative links in the Markdown files that point at a file that does not exist: [{from, url}]."""
    present = {i['path'] for i in scan(root)} | {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    broken = []
    for item in scan(root):
        if item['kind'] == 'markdown':
            _html, _toc, links = render_markdown((root / item['path']).read_text(encoding='utf-8', errors='replace'), item['path'], lambda t: t in present)
            broken += [{'from': l['from'], 'url': l['url']} for l in links if not l['exists']]
    return broken


# ------------------------------------------------------------------------------------------------------ search
def search(query, root=ROOT, limit=40):
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []
    results = []
    for item in scan(root):
        if Path(item['path']).suffix.lower() not in SEARCH_EXT or item['size'] > MAX_TEXT:
            continue
        text = (root / item['path']).read_text(encoding='utf-8', errors='replace')
        lower = text.lower()
        if not all(t in lower for t in terms):
            continue
        used, anchor, in_fence, snippets = set(), '', False, []
        for number, line in enumerate(text.splitlines(), 1):
            if FENCE.match(line):
                in_fence = not in_fence
            elif not in_fence and (h := HEADING.match(line)):
                anchor = slugify(h.group(2), used)
            if len(snippets) < 3 and any(t in line.lower() for t in terms):
                snippets.append({'line': number, 'text': line.strip()[:220], 'anchor': anchor if item['kind'] == 'markdown' else ''})
        score = sum(lower.count(t) for t in terms) + (25 if all(t in item['title'].lower() for t in terms) else 0)
        results.append({'path': item['path'], 'title': item['title'], 'kind': item['kind'], 'score': score, 'matches': snippets})
    return sorted(results, key=lambda r: -r['score'])[:limit]


def document(rel, root=ROOT):
    """What the page needs to show one file, or None."""
    target = resolve(rel, root)
    if not target:
        return None
    path, kind = target.relative_to(root.resolve()).as_posix(), kind_of(target.name)
    url = '/files/' + quote(path)
    if kind in ('image', 'pdf', 'download', 'other'):
        return {'kind': kind, 'path': path, 'title': target.name, 'url': url}
    if target.stat().st_size > MAX_TEXT:
        return {'kind': 'download', 'path': path, 'title': target.name, 'url': url}
    text = target.read_text(encoding='utf-8', errors='replace')
    if kind == 'markdown':
        present = {p.relative_to(root.resolve()).as_posix() for p in root.resolve().rglob('*') if p.is_file()}
        body, toc, _links = render_markdown(text, path, lambda t: t in present)
        return {'kind': 'markdown', 'path': path, 'title': doc_title(target, text), 'html': body, 'toc': toc, 'url': url}
    return {'kind': kind, 'path': path, 'title': target.name, 'text': text, 'url': url}


# ------------------------------------------------------------------------------------------------------ server
SHELL = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Documentation</title>
<style>
:root{--bg:#f6f7fb;--panel:#fff;--text:#1b2033;--muted:#667089;--line:#e2e6f0;--accent:#2b3d89;--code:#f0f2f8;--warn:#b3322a;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#0e1220;--panel:#151b2e;--text:#e6e9f5;--muted:#8f98b5;--line:#252d45;--accent:#8fa2ff;--code:#1c243b;--warn:#ff8a80;color-scheme:dark}}
*{box-sizing:border-box}html,body{height:100%;margin:0}body{font:15px/1.6 "Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--text);display:flex;flex-direction:column}
header{display:flex;gap:.75rem;align-items:center;padding:.6rem 1rem;background:var(--panel);border-bottom:1px solid var(--line)}
header h1{font-size:1rem;margin:0;white-space:nowrap}header input{flex:1;max-width:32rem;font:inherit;padding:.4rem .7rem;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text)}
header button{font:inherit;padding:.4rem .8rem;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--text);cursor:pointer}
.layout{flex:1;display:flex;min-height:0}nav{width:19rem;overflow:auto;padding:.75rem;border-right:1px solid var(--line);background:var(--panel);font-size:.9rem}
nav ul{list-style:none;margin:0;padding-left:.9rem}nav>ul{padding-left:0}nav summary{cursor:pointer;font-weight:600;padding:.2rem 0;color:var(--muted)}
nav a{display:block;padding:.18rem .5rem;border-radius:6px;color:var(--text);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
nav a:hover{background:var(--bg)}nav a.active{background:var(--accent);color:var(--panel)}nav a::before{content:attr(data-badge);display:inline-block;min-width:2.2rem;margin-right:.35rem;font-size:.65rem;font-weight:700;color:var(--muted);text-transform:uppercase}
nav a.active::before{color:inherit}main{flex:1;overflow:auto;padding:1.25rem 2rem}.content{max-width:60rem;margin:0 auto}
aside#toc{width:15rem;overflow:auto;padding:1rem .75rem;font-size:.82rem;border-left:1px solid var(--line)}aside#toc a{display:block;color:var(--muted);text-decoration:none;padding:.12rem 0}aside#toc a:hover{color:var(--accent)}aside#toc .l3{padding-left:.9rem}
@media (max-width:1100px){aside#toc{display:none}}@media (max-width:760px){nav{display:none}}
h1,h2,h3,h4{line-height:1.25;scroll-margin-top:1rem}h2{margin-top:2rem;padding-bottom:.25rem;border-bottom:1px solid var(--line)}a{color:var(--accent)}a.broken,.broken{color:var(--warn)}
code{background:var(--code);padding:.1rem .35rem;border-radius:5px;font:.88em Consolas,monospace}pre.code{background:var(--code);padding:.8rem 1rem;border-radius:8px;overflow:auto}pre.code code{background:none;padding:0}
.table-scroll{overflow-x:auto;margin:1rem 0}table{border-collapse:collapse;min-width:50%}th,td{border:1px solid var(--line);padding:.4rem .65rem;text-align:left;vertical-align:top}th{background:var(--code)}
img{max-width:100%;height:auto;background:#fff;border-radius:6px}blockquote{margin:1rem 0;padding:.1rem 1rem;border-left:4px solid var(--accent);color:var(--muted)}
pre.mermaid{background:var(--panel);padding:1rem;border:1px solid var(--line);border-radius:8px;overflow:auto;text-align:center;font:.85rem Consolas,monospace;white-space:pre}pre.mermaid.failed{text-align:left;color:var(--text);background:var(--code)}
.note{color:var(--muted);font-size:.85rem}.result{padding:.7rem 0;border-bottom:1px solid var(--line)}.result .snip{color:var(--muted);font-size:.85rem}.result mark{background:#ffe58a;color:#222}
iframe{width:100%;height:80vh;border:1px solid var(--line);border-radius:8px}.empty{color:var(--muted);padding:2rem 0}
</style></head><body>
<header><h1>Documentation</h1><input id="q" type="search" placeholder="Search all documents" aria-label="Search all documents"><button id="refresh" title="Re-read the folder">Refresh</button><span id="status" class="note"></span></header>
<div class="layout"><nav id="tree" aria-label="Documents"></nav><main id="main"><div class="content" id="content"><p class="empty">Loading…</p></div></main><aside id="toc" aria-label="On this page"></aside></div>
<script>
'use strict';
const $ = (s) => document.querySelector(s);
const BADGES = {markdown: 'doc', diagram: 'diag', image: 'img', pdf: 'pdf', download: 'file', text: 'text', other: 'file'};
const MERMAID_SOURCES = ['/files/vendor/mermaid.min.js', '__CDN__'];
let tree = {items: [], signature: ''}, current = null, currentModified = 0, mermaidPromise = null;
const openFolders = new Set(['']);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

async function api(path) {
  const response = await fetch(path, {cache: 'no-store'});
  if (!response.ok) throw new Error((await response.text()) || response.statusText);
  return response.json();
}

function buildTree(items) {
  const root = {files: [], dirs: {}};
  for (const item of items) {
    const parts = item.path.split('/');
    let node = root;
    for (const dir of parts.slice(0, -1)) node = node.dirs[dir] = node.dirs[dir] || {files: [], dirs: {}};
    node.files.push(item);
  }
  return root;
}

function renderNode(node, prefix) {
  let out = '<ul>';
  for (const item of node.files) {
    const active = item.path === current ? ' active' : '';
    out += `<li><a href="#/${encodeURI(item.path)}" data-badge="${BADGES[item.kind] || 'file'}" class="${active.trim()}" title="${esc(item.path)}">${esc(item.title)}</a></li>`;
  }
  for (const name of Object.keys(node.dirs)) {
    const path = prefix + name + '/';
    out += `<li><details data-folder="${esc(path)}"${openFolders.has(path) || containsActive(node.dirs[name]) ? ' open' : ''}><summary>${esc(name)}</summary>${renderNode(node.dirs[name], path)}</details></li>`;
  }
  return out + '</ul>';
}
const containsActive = (node) => node.files.some((f) => f.path === current) || Object.values(node.dirs).some(containsActive);

function renderTree() {
  const el = $('#tree');
  el.innerHTML = tree.items.length ? renderNode(buildTree(tree.items), '') : '<p class="empty">No documents found.</p>';
  for (const d of el.querySelectorAll('details')) d.addEventListener('toggle', () => (d.open ? openFolders.add(d.dataset.folder) : openFolders.delete(d.dataset.folder)));
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src; s.onload = resolve; s.onerror = () => reject(new Error('could not load ' + src));
    document.head.appendChild(s);
  });
}

function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = (async () => {
      for (const src of MERMAID_SOURCES) {
        try {
          await loadScript(src);
          if (window.mermaid) {
            window.mermaid.initialize({startOnLoad: false, securityLevel: 'strict', theme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'default'});
            return window.mermaid;
          }
        } catch (e) { /* try the next source */ }
      }
      return null;
    })();
  }
  return mermaidPromise;
}

async function drawDiagrams() {
  const nodes = Array.from(document.querySelectorAll('#content pre.mermaid'));
  if (!nodes.length) return;
  const mermaid = await loadMermaid();
  for (const node of nodes) {
    if (!mermaid) { node.classList.add('failed'); continue; }
    try { await mermaid.run({nodes: [node]}); }
    catch (e) { node.classList.add('failed'); node.insertAdjacentHTML('beforebegin', `<p class="note broken">This diagram could not be drawn: ${esc(e && e.message || e)}. Its source is shown below.</p>`); }
  }
  if (!mermaid) $('#content').insertAdjacentHTML('afterbegin', '<p class="note">Diagrams are shown as source: Mermaid could not be loaded (no internet, and no copy at docs/vendor/mermaid.min.js).</p>');
}

function renderToc(toc) {
  $('#toc').innerHTML = toc.filter((h) => h.level >= 2 && h.level <= 3).map((h) => `<a class="l${h.level}" href="#/${encodeURI(current)}#${h.id}">${esc(h.text)}</a>`).join('');
}

async function loadDoc(path, anchor, keepScroll) {
  const main = $('#main'), scroll = main.scrollTop;
  let d;
  try { d = await api('/api/doc?path=' + encodeURIComponent(path)); }
  catch (e) { $('#content').innerHTML = `<p class="empty">Could not open <code>${esc(path)}</code>.</p>`; return; }
  current = path; currentModified = (tree.items.find((i) => i.path === path) || {}).modified || 0;
  let body = '';
  if (d.kind === 'markdown') body = `<article>${d.html}</article>`;
  else if (d.kind === 'diagram') body = `<h2>${esc(d.title)}</h2><pre class="mermaid">${esc(d.text)}</pre>`;
  else if (d.kind === 'image') body = `<h2>${esc(d.title)}</h2><p><a href="${d.url}" target="_blank" rel="noopener">Open full size</a></p><img src="${d.url}" alt="${esc(d.title)}">`;
  else if (d.kind === 'pdf') body = `<h2>${esc(d.title)}</h2><p><a href="${d.url}" target="_blank" rel="noopener">Open in a new tab</a></p><iframe src="${d.url}" title="${esc(d.title)}"></iframe>`;
  else if (d.kind === 'text') body = `<h2>${esc(d.title)}</h2><pre class="code"><code>${esc(d.text)}</code></pre>`;
  else body = `<h2>${esc(d.title)}</h2><p>This file type cannot be shown here. <a href="${d.url}" download>Download ${esc(d.title)}</a></p>`;
  $('#content').innerHTML = body;
  renderToc(d.toc || []);
  renderTree();
  document.title = d.title + ' · Documentation';
  if (anchor) { const target = document.getElementById(anchor); if (target) target.scrollIntoView(); }
  else main.scrollTop = keepScroll ? scroll : 0;
  drawDiagrams();
}

function highlight(text, terms) {
  let out = esc(text);
  for (const t of terms) out = out.replace(new RegExp('(' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig'), '<mark>$1</mark>');
  return out;
}

async function showSearch(query) {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const results = await api('/api/search?q=' + encodeURIComponent(query));
  $('#toc').innerHTML = '';
  $('#content').innerHTML = `<h2>${results.length} document(s) match “${esc(query)}”</h2>` + (results.length ? results.map((r) => `<div class="result"><a href="#/${encodeURI(r.path)}${r.matches[0] && r.matches[0].anchor ? '#' + r.matches[0].anchor : ''}"><strong>${esc(r.title)}</strong></a> <span class="note">${esc(r.path)}</span>${r.matches.map((m) => `<div class="snip">${m.anchor ? `<a href="#/${encodeURI(r.path)}#${m.anchor}">line ${m.line}</a>` : 'line ' + m.line}: ${highlight(m.text, terms)}</div>`).join('')}</div>`).join('') : '<p class="empty">Nothing found. Every word must appear in the document.</p>');
}

function route() {
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash.startsWith('?q=')) { const q = hash.slice(3); $('#q').value = q; return q.trim() ? showSearch(q.trim()) : undefined; }
  const [path, anchor] = hash.startsWith('/') ? hash.slice(1).split('#') : [defaultDoc(), ''];
  if (!path) return undefined;
  if (path === current && anchor && document.getElementById(anchor)) { document.getElementById(anchor).scrollIntoView(); return undefined; }
  return loadDoc(path, anchor, false);
}
const defaultDoc = () => (tree.items.find((i) => /^readme\.md$/i.test(i.path)) || tree.items.find((i) => i.kind === 'markdown') || {}).path;

async function refreshTree(initial) {
  let next;
  try { next = await api('/api/tree'); } catch (e) { $('#status').textContent = 'Server not reachable'; return; }
  $('#status').textContent = '';
  if (next.signature === tree.signature && !initial) return;
  tree = next; renderTree();
  const me = tree.items.find((i) => i.path === current);
  if (!initial && me && me.modified !== currentModified && !location.hash.startsWith('#?q=')) loadDoc(current, '', true);   // reload the open document when it changes on disk
}

let timer;
$('#q').addEventListener('input', (e) => { clearTimeout(timer); timer = setTimeout(() => { const q = e.target.value.trim(); location.hash = q ? '#?q=' + encodeURIComponent(q) : (current ? '#/' + encodeURI(current) : ''); }, 250); });
$('#refresh').addEventListener('click', () => refreshTree(true).then(route));
window.addEventListener('hashchange', route);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshTree(false); });
setInterval(() => refreshTree(false), 6000);
refreshTree(true).then(route);
</script></body></html>'''.replace('__CDN__', MERMAID_CDN)


class Handler(BaseHTTPRequestHandler):
    server_version = 'DocsViewer'
    allowed_hosts = {'localhost', '127.0.0.1', '[::1]'}

    def log_message(self, *args):   # quiet: this is a local reading tool
        pass

    def send_bytes(self, data, content_type, status=200, headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj).encode('utf-8'), 'application/json; charset=utf-8', status)

    def do_GET(self):
        # Refuse requests whose Host is not this computer: stops a web page elsewhere from reading these files via DNS rebinding.
        if self.headers.get('Host', '').rsplit(':', 1)[0].lower() not in self.allowed_hosts:
            return self.send_json({'error': 'Unexpected host'}, 421)
        url = urlparse(self.path)
        query = parse_qs(url.query)
        root = self.server.root
        if url.path == '/':
            return self.send_bytes(SHELL.encode('utf-8'), 'text/html; charset=utf-8')
        if url.path == '/api/tree':
            items = scan(root)
            return self.send_json({'root': root.name, 'signature': signature(items), 'items': items})
        if url.path == '/api/doc':
            doc = document((query.get('path') or [''])[0], root)
            return self.send_json(doc) if doc else self.send_json({'error': 'Not found'}, 404)
        if url.path == '/api/search':
            return self.send_json(search((query.get('q') or [''])[0], root))
        if url.path.startswith('/files/'):
            target = resolve(url.path[len('/files/'):], root)
            if not target:
                return self.send_json({'error': 'Not found'}, 404)
            content_type = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            if target.suffix.lower() in ('.mmd', '.sql', '.dot', '.ps1', '.cmd', '.md', '.py', '.yml', '.yaml', '.csv', '.log', '.ini'):
                content_type = 'text/plain; charset=utf-8'
            headers = {'Content-Disposition': 'attachment'} if target.suffix.lower() in ('.docx', '.xlsx', '.pptx', '.zip') else {}
            if target.suffix.lower() == '.svg':
                headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"   # an SVG may not run scripts
            return self.send_bytes(target.read_bytes(), content_type, headers=headers)
        return self.send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        # Read and throw away whatever was sent BEFORE answering. Answering and closing with the request body still unread makes Windows reset the
        # connection, so the client sees "connection aborted" instead of the 405 (the documentation test failed that way on 12 Nov 2026).
        try:
            length = min(int(self.headers.get('Content-Length') or 0), 1 << 20)
        except ValueError:
            length = 0
        while length > 0:
            chunk = self.rfile.read(min(length, 65536))
            if not chunk:
                break
            length -= len(chunk)
        self.close_connection = True
        self.send_bytes(json.dumps({'error': 'Read-only'}).encode('utf-8'), 'application/json; charset=utf-8', 405, {'Allow': 'GET, HEAD'})

    do_PUT = do_DELETE = do_PATCH = do_POST


def make_server(root=ROOT, host='127.0.0.1', port=8765, tries=20):
    last = None
    for candidate in range(port, port + tries):
        try:
            server = ThreadingHTTPServer((host, candidate), Handler)
            server.root = Path(root).resolve()
            return server
        except OSError as exc:
            last = exc
    raise SystemExit(f'No free port from {port} to {port + tries - 1}: {last}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='View the documentation in this folder.')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', default='127.0.0.1', help='Default: this computer only. Anything else lets others on the network read these files.')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        Handler.allowed_hosts = Handler.allowed_hosts | {args.host.lower()}
        print(f'WARNING: listening on {args.host}: anyone who can reach it can read the documentation folder.')
    server = make_server(ROOT, args.host, args.port)
    url = f'http://{"localhost" if args.host in ("127.0.0.1", "localhost") else args.host}:{server.server_address[1]}/'
    print(f'Documentation viewer: {url}\nFolder: {ROOT}\nNew and changed files appear on their own. Press Ctrl+C to stop.')
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
