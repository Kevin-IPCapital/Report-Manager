# Running collection unattended

Collection needs two things running: the **worker** (every minute: runs queued "Update all" requests and due jobs, one at a
time, and records a heartbeat) and the **alert check** (every 5 minutes: emails problems). Neither does anything until a job has
`"schedule_enabled": true` in `backend/collector_jobs.json`. That file is yours: it is **not shipped** (so an archive can never overwrite your settings); create it once with `copy backend\collector_jobs.example.json backend\collector_jobs.json`. The example has the recommended values.

## Choose how to run them

| Option | Needs | Good for |
|---|---|---|
| **A. `run_scheduler`** (one command, no scripts) | A console, or any way to start a program at boot | Getting going now; machines where scripts are blocked |
| **B. Windows Task Scheduler** (two tasks) | Rights to create tasks | A server that should restart by itself |

### A. `python manage.py run_scheduler`
From `backend`, with the virtual environment active. It starts the worker every minute and runs the alert check every 5 minutes,
in one process, until you press Ctrl+C. A second copy refuses to start. `--worker-every` and `--alerts-every` change the seconds;
`--once` does one tick of each and exits.

- It is a separate process on purpose, not a timer inside the web server. A web server may run several processes (each would start
  its own timer), restarts when code changes, and recycles workers. The collectors also take hours and must not run inside a web request.
- A long collection does not delay the alert check, and the scheduler never starts a second worker while one is running.
- It does not restart itself. To start it automatically at boot, use Task Scheduler's GUI (B below) with the action
  `cmd /c "cd /d <project>\backend && .venv\Scripts\python.exe manage.py run_scheduler"` and the trigger "At startup".

### B. Task Scheduler
**If `Register-CollectorTasks.ps1` says it is "not digitally signed" or scripts are disabled**, that is PowerShell's execution policy,
not a problem with the script. Check with `Get-ExecutionPolicy -List`, then try in this order:
1. `Unblock-File .\Register-CollectorTasks.ps1` (clears the "downloaded from the internet" mark; enough for `RemoteSigned`).
2. `powershell -ExecutionPolicy Bypass -File .\Register-CollectorTasks.ps1 -WhatIf` (applies to that one run only).
3. If a Group Policy sets the policy to `AllSigned`, neither works; the script would have to be signed with your organisation's
   certificate. Use option A, or create the tasks by hand in the Task Scheduler GUI.

By hand in the GUI (Task Scheduler, Create Task):
- Worker: action = program `...\backend\.venv\Scripts\python.exe`, arguments `manage.py collection_worker`, start in `...\backend`;
  trigger = daily, repeat every 1 minute indefinitely; settings: "Do not start a new instance" if already running.
- Alerts: the same with `manage.py collection_alerts`, repeating every 5 minutes.
- "Run whether user is logged on or not".

### About APScheduler and cron
- **APScheduler** would run the same two ticks from a Python process, but this project already has the scheduling rules it needs
  (a repeat interval per job, held in `collector_jobs.json`), so `run_scheduler` is a plain loop with no extra package. APScheduler
  becomes worth adding if you want fixed times ("every day at 02:00") instead of intervals.
- **cron** does not exist on Windows; Task Scheduler (B) is its equivalent. On Linux, a crontab line running
  `manage.py collection_worker` every minute and `manage.py collection_alerts` every 5 minutes does the same job.

## Set up
1. The backend runs with its virtual environment and `backend/.env` filled in (`python manage.py dbcheck` passes).
2. Check the alert email works. In `backend/.env` set `COLLECTOR_ALERT_EMAILS` and the `EMAIL_*` values, then:
   `python manage.py collection_alerts --test-email`
   Until real `EMAIL_*` settings exist, mail goes to the backend console and nobody receives it.
3. Start option A or B above. For B with the script, from an elevated PowerShell in `backend\deploy`:
   `.\Register-CollectorTasks.ps1 -WhatIf`, then `.\Register-CollectorTasks.ps1` (`-Remove` undoes it).
4. Prove it: open **Data Collection**. The line "Worker last ran ... ago" should show a recent time.
5. Switch a job on when you are ready: set `schedule_enabled` to `true` (the values in `collector_jobs.example.json` are the
   recommended ones). A job runs on the next tick, then every `interval_minutes`. Do this one job at a time, N-central first.

## What gets emailed
Each problem is emailed once, again as a reminder every `COLLECTOR_ALERT_REPEAT_HOURS` (24) while it lasts, and once more
when it clears. If the email cannot be sent, nothing is recorded and the next run tries again.

| Problem | When |
|---|---|
| Worker never ran / not running | Something is scheduled and the heartbeat is missing or older than `COLLECTOR_HEARTBEAT_MAX_MINUTES` (5) |
| Collector failed | An enabled job's last run failed |
| Collector stuck | A run started and never finished within 1.5 times its time limit |
| Data overdue | A scheduled job's stored data is older than 1.5 times its interval plus 10 minutes |
| No stored data | A scheduled job has produced nothing yet |
| Partial result | Off by default (`COLLECTOR_ALERT_ON_PARTIAL=1` to turn on): the N-central 404 policy is not decided |

`python manage.py collection_alerts --dry-run` prints what would be sent without sending or recording anything. The same list
shows at the top of the Data Collection page.

## Good to know
- The worker records a heartbeat every 30 seconds while a collector runs, so a long collection never looks like a dead worker.
  A tick that finds the worker still busy exits normally and records a "busy" heartbeat, so Task Scheduler does not fill with failures.
- The tasks run as the user you give (default: you), whether or not they are logged on. They need read and write access to
  the project folder, since logs and the queue database live in `backend\logs` and `backend\collector_runtime.sqlite3`.
- Queue state is a local file. Run the API and the worker on the same machine (or the same shared folder).
- If you move the project, run the script again (`-Force` replaces the old tasks).
