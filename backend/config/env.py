"""Tiny .env loader and DATABASE_URL parser (no third-party packages)."""
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse


def load_env(path):
    """Load KEY=VALUE lines into os.environ. Real environment variables win over the file."""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_from_url(url, base_dir):
    """postgres://user:password@host:5432/dbname?sslmode=require  ->  Django DATABASES entry.
    Special characters in the user or password must be percent-encoded (@ -> %40, : -> %3A, / -> %2F)."""
    parts = urlparse(url)
    if parts.scheme in ('postgres', 'postgresql'):
        return {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': unquote(parts.path.lstrip('/')),
            'USER': unquote(parts.username or ''),
            'PASSWORD': unquote(parts.password or ''),
            'HOST': parts.hostname or '',
            'PORT': parts.port or '',
            'OPTIONS': dict(parse_qsl(parts.query)),
        }
    if parts.scheme == 'sqlite':  # sqlite:///db.sqlite3 is relative to the backend folder; sqlite:///:memory: lives in memory
        name = unquote(parts.path[1:])
        return {'ENGINE': 'django.db.backends.sqlite3', 'NAME': name if name == ':memory:' else Path(base_dir) / name}
    raise ValueError(f'Unsupported database URL scheme {parts.scheme!r}: use postgres:// or sqlite:///')


def without_search_path(config):
    """A copy of a database entry with any `-c search_path=...` option removed (other options are kept).

    A test run creates a brand-new database, which does not contain the schema that search_path points at; with it
    PostgreSQL answers "no schema has been selected to create in". Without it the tables go in `public`, which the
    database's owner (the role that created the test database) may always write to."""
    options = dict(config.get('OPTIONS') or {})
    if 'options' in options:
        remaining = re.sub(r'-c\s+search_path=\S+', '', options['options']).strip()
        if remaining:
            options['options'] = remaining
        else:
            del options['options']
    return {**config, 'OPTIONS': options}
