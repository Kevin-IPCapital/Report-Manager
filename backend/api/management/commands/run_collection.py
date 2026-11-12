"""Run one allowlisted legacy data collector in a subprocess.

Designed for Windows Task Scheduler or cron; NEVER start this as a background
thread inside the Django API. This command does not modify source schemas.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from .collection_outcomes import classify_outcome

# Explicit allowlist prevents arbitrary shell execution through job configuration.
# Jobs are disabled by default and must be opted into individually.
JOB_COMMANDS = {
    "ncentral_inventory": ("ncentral", "ncentral_inventory.py"),
    "ncentral_patches": ("ncentral", "ncentral_patch.py", "collect"),
    "ncentral_monitoring": ("ncentral", "ncentral_observability.py", "monitoring"),
    "autotask_tickets": ("autotask", "autotask_db.py", "load-active", "--days", "120", "--limit", "500"),
    "sentinelone_monthly": ("sentinelone", "s1_db.py", "load-active"),
    "cove_backup": ("cove", "cove_db.py", "load-active"),
}

# These scripts ignore a --dry-run flag, so passing it would run a REAL load. Refuse instead.
NO_DRY_RUN = frozenset({"autotask_tickets", "sentinelone_monthly", "cove_backup"})

# Cross-platform process lock (non-blocking) with a persistent lock file.
class JobLock:
    def __init__(self, path):
        self.path = path
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                if self.file.read(1) == b"":
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            self.file.close()
            self.file = None
            raise RuntimeError("Another instance of this job is running")
        return self

    def __exit__(self, *args):
        if self.file:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()


def load_job_config(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or "jobs" not in config:
        raise ValueError("Expected JSON object containing jobs")
    return config


def format_log(status, job, elapsed, code=None, extra=""):
    return (f'{datetime.now(timezone.utc).isoformat()} '
            f'{status} job={job} duration_seconds={elapsed:.3f} '
            f'exit_code={code} {extra}\n')


class Command(BaseCommand):
    help = "Run an explicitly enabled external collector, with lock, timeout and append-only logging."

    def add_arguments(self, parser):
        parser.add_argument("job", choices=sorted(JOB_COMMANDS))
        parser.add_argument("--config", default=None, help="JSON path; defaults to backend/collector_jobs.json")
        parser.add_argument("--dry-run", action="store_true", help="Pass supported dry-run flag to N-central collectors (still requests API)")

    def handle(self, *args, **options):
        name = options["job"]
        path = Path(options["config"] or settings.BASE_DIR / "collector_jobs.json").resolve()
        if not path.is_file():
            raise CommandError(f"{path.name} not found. Copy collector_jobs.example.json to collector_jobs.json (in the backend folder) and adjust it.")
        try:
            config = load_job_config(path)
            job = config["jobs"].get(name)
            if not isinstance(job, dict) or not job.get("enabled", False):
                raise CommandError(f"Collector {name} disabled or missing from {path}")
            source_dir, filename, *fixed_args = JOB_COMMANDS[name]
            roots = config.get("source_directories", {})
            if source_dir not in roots:
                raise CommandError(f"Missing source_directories.{source_dir}")
            folder = Path(roots[source_dir]).expanduser()
            if not folder.is_absolute():
                folder = path.parent / folder
            folder = folder.resolve()
            script = folder / filename
            if not script.is_file():
                raise CommandError(f"Collector script not found: {script}")
            if options["dry_run"] and name in NO_DRY_RUN:
                raise CommandError(f"{name} has no safe dry-run mode. No request made.")
            timeout = int(job.get("timeout_seconds", 1800))
            if not 1 <= timeout <= 7200:
                raise CommandError("timeout_seconds must be between 1 and 7200")
            extra = job.get("args", [])
            if not isinstance(extra, list) or any(not isinstance(x, str) for x in extra):
                raise CommandError("args must be an array of strings")
            # Never permit dynamically configured destructive or initialization commands.
            if any(x in ("init", "sync-customers") for x in extra):
                raise CommandError("Unsafe operation in configured args")
            argv = [sys.executable, str(script), *fixed_args, *extra]
            if options["dry_run"]:
                argv.append("--dry-run")
            log_dir = (settings.BASE_DIR / "logs" / "collectors")
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{name}.log"
            start = time.monotonic()
            with JobLock(log_dir / f"{name}.lock"):
                # Append logs directly to file. No in-memory capture of credentials or personal data.
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(format_log("START", name, 0, extra=f'dry_run={options["dry_run"]}'))
                    fh.flush()
                    try:
                        # Track the position before subprocess output to classify THIS run
                        # without accidentally matching older log entries.
                        output_start = fh.buffer.tell()
                        result = subprocess.run(argv, cwd=folder, stdout=fh, stderr=subprocess.STDOUT,
                                                timeout=timeout, check=False, shell=False)
                        fh.flush()
                        # Read only this execution's output. A size cap avoids loading
                        # an unbounded subprocess log into memory.
                        output = ""
                        if result.returncode != 0:
                            with log_path.open("rb") as reader:
                                reader.seek(output_start)
                                raw = reader.read(2_000_001)
                                if len(raw) > 2_000_000:
                                    output = "Output exceeds safe classification limit"
                                else:
                                    output = raw.decode("utf-8", errors="replace")
                        elapsed = time.monotonic() - start
                        outcome = classify_outcome(name, result.returncode, output)
                        extra_fields = f'dry_run={options["dry_run"]} reason={outcome.reason!r}'
                        if outcome.unavailable is not None:
                            extra_fields += (f' devices={outcome.devices} entries={outcome.entries}'
                                             f' unavailable={outcome.unavailable}')
                        fh.write(format_log(outcome.status, name, elapsed, result.returncode,
                                            extra=extra_fields))
                        fh.flush()
                        if outcome.status == "FAILED":
                            raise CommandError(f"{name} returned {result.returncode}; see {log_path}")
                        if outcome.status == "PARTIAL":
                            self.stdout.write(self.style.WARNING(
                                f"{name} PARTIAL in {elapsed:.2f}s: {outcome.unavailable} "
                                f"devices cannot be found in N-central (disconnected); log: {log_path}"))
                        else:
                            self.stdout.write(self.style.SUCCESS(
                                f"{name} finished in {elapsed:.2f}s; log: {log_path}"))
                    except subprocess.TimeoutExpired as exc:
                        elapsed = time.monotonic() - start
                        fh.write(format_log("FAILED", name, elapsed,
                                            extra=f'reason=timeout limit_seconds={timeout}'))
                        fh.flush()
                        raise CommandError(f"{name} timed out; see {log_path}") from exc
                    except OSError as exc:
                        elapsed = time.monotonic() - start
                        fh.write(format_log("FAILED", name, elapsed,
                                            extra=f'reason=subprocess_error error_type={type(exc).__name__}'))
                        fh.flush()
                        raise CommandError(f"{name} could not start; see {log_path}") from exc
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            raise CommandError(str(exc)) from exc
