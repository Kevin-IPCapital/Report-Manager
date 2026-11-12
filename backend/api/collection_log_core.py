"""Bounded read-only access to the most recent collector execution log."""
from pathlib import Path
from .collection_health_core import JOB_NAMES, LOG_LINE

MAX_READ_BYTES = 512 * 1024
MAX_OUTPUT_CHARS = 60000


def latest_run_log(base_dir, job):
    """Return last START...terminal block; never open arbitrary user-supplied paths."""
    if job not in JOB_NAMES:
        raise ValueError('Unknown collector')
    path = Path(base_dir) / 'logs' / 'collectors' / f'{job}.log'
    try:
        with path.open('rb') as fh:
            fh.seek(0, 2)
            size = fh.tell()
            offset = max(0, size - MAX_READ_BYTES)
            fh.seek(offset)
            raw = fh.read(MAX_READ_BYTES)
    except FileNotFoundError:
        return {'job': job, 'available': False, 'log': '', 'truncated': False}
    if offset:
        raw = raw.partition(b'\n')[2]  # avoid half a line at chunk boundary
    lines = raw.decode('utf-8', 'replace').splitlines()
    starts = [i for i, line in enumerate(lines) if (m := LOG_LINE.match(line)) and m['job'] == job and m['status'] == 'START']
    # A latest START always wins, even if it has no terminal marker yet.
    if starts:
        lines = lines[starts[-1]:]
    else:
        # For legacy logs without a START marker, show the bounded tail.
        lines = lines[-120:]
    log = '\n'.join(lines)
    truncated = offset > 0 or len(log) > MAX_OUTPUT_CHARS
    if len(log) > MAX_OUTPUT_CHARS:
        log = '… [earlier lines omitted]\n' + log[-MAX_OUTPUT_CHARS:]
        log = log.partition('\n')[2]  # remove half line at cut
    return {'job': job, 'available': bool(lines), 'log': log, 'truncated': truncated}
