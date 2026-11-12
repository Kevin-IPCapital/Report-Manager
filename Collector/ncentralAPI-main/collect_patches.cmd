@echo off
REM ---------------------------------------------------------------------------
REM Daily patch collection. Point Task Scheduler at this file.
REM
REM Daily, not hourly: patching is an inventory, and each device costs a ~200 KB
REM asset fetch. Nothing meaningful changes within an hour.
REM
REM cd first: the client reads .env from the current working directory, so a task
REM launched from C:\Windows\System32 would find no credentials.
REM ---------------------------------------------------------------------------

set PYTHON=C:\Users\MikeLai\.conda\envs\ipcipd314\python.exe
set PROJECT=%~dp0

cd /d "%PROJECT%"
"%PYTHON%" ncentral_patch.py collect --quiet >> "%PROJECT%patch.log" 2>&1

exit /b %ERRORLEVEL%
