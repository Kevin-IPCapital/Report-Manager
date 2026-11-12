@echo off
REM ---------------------------------------------------------------------------
REM Hourly performance poll. Point Task Scheduler (or your cron) at this file.
REM
REM cd first: the client reads .env from the current working directory, so a
REM scheduled task launched from C:\Windows\System32 would find no credentials.
REM ---------------------------------------------------------------------------

set PYTHON=C:\Users\MikeLai\.conda\envs\ipcipd314\python.exe
set PROJECT=%~dp0

cd /d "%PROJECT%"
"%PYTHON%" ncentral_performance.py collect --quiet >> "%PROJECT%collect.log" 2>&1

REM Non-zero means at least one customer failed; Task Scheduler surfaces it as
REM the Last Run Result, so a silently broken collector stays visible.
exit /b %ERRORLEVEL%
