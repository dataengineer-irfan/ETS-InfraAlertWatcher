@echo off
REM Runs the daily ingest -> expiry check -> email pipeline.
REM Resolves its own location, so it works from any checkout and from
REM Task Scheduler (which starts in C:\Windows\System32 by default).
cd /d "%~dp0"
call venv\Scripts\activate.bat
python src\run_daily.py >> data\run_log.txt 2>&1
