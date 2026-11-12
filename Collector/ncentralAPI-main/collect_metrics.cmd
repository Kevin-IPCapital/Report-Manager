@echo off
REM ---------------------------------------------------------------------------
REM Measured CPU/RAM readings. Point Task Scheduler at this file.
REM
REM Schedule this to match the Automation Manager policy that writes the
REM readings, not more often. A custom property holds one value — the last one
REM written — so polling faster than the AMP samples re-reads the same reading
REM and stores nothing new. AMP every 15 minutes, this every 15 minutes.
REM
REM Separate from collect_performance.cmd on purpose: sampling cadence is yours
REM to pick, and a slow metrics pass must not delay the hourly poll.
REM
REM cd first: the client reads .env from the current working directory, so a
REM scheduled task launched from C:\Windows\System32 would find no credentials.
REM ---------------------------------------------------------------------------

set PYTHON=C:\Users\MikeLai\.conda\envs\ipcipd314\python.exe
set PROJECT=%~dp0

cd /d "%PROJECT%"
"%PYTHON%" ncentral_metrics.py collect --quiet >> "%PROJECT%metrics.log" 2>&1

REM Non-zero means at least one customer failed; Task Scheduler surfaces it as
REM the Last Run Result, so a silently broken collector stays visible.
exit /b %ERRORLEVEL%
