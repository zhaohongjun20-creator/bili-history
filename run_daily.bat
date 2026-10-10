@echo off
rem Daily task entry for bili-history: full pipeline, log appended to logs\task.log
rem NOTE: keep CRLF line endings and ASCII-only comments (cmd parses UTF-8/LF bat badly)
rem Proxy strategy: Wikimedia images need intl access. If local proxy 7890 is
rem alive we route through it, otherwise go direct (domestic GLM and edge-tts still work).
rem Probe: python is guaranteed present because the pipeline itself needs it.
python -c "import socket;exit(0 if socket.socket().connect_ex(('127.0.0.1',7890))==0 else 1)" >nul 2>&1
if %errorlevel%==0 (
  set HTTP_PROXY=http://127.0.0.1:7890
  set HTTPS_PROXY=http://127.0.0.1:7890
) else (
  set NO_PROXY=*
  set HTTP_PROXY=
  set HTTPS_PROXY=
)
cd /d %~dp0
if not exist logs mkdir logs
python autopost.py >> logs\task.log 2>&1
