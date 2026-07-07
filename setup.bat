@echo off
REM ====================================================================
REM  Shorts Clipper - Windows setup. Just DOUBLE-CLICK this file.
REM ====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo(
echo ============================================
echo    Shorts Clipper - Setup
echo ============================================
echo(

REM --- 1. Find Python ---------------------------------------------------
set "PY="
where py    >nul 2>nul && set "PY=py"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo [X] Python is not installed.
  echo(
  echo     1^) Get it from https://www.python.org/downloads/
  echo     2^) During install, TICK the box "Add Python to PATH"
  echo     3^) Then double-click this file again.
  echo(
  pause
  exit /b 1
)
echo [OK] Python found.

REM --- 2. Find ffmpeg ---------------------------------------------------
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [X] ffmpeg is not installed ^(this does the video work^).
  echo(
  echo     Open PowerShell and run:   winget install ffmpeg
  echo     Then CLOSE it, and double-click this file again.
  echo(
  pause
  exit /b 1
)
echo [OK] ffmpeg found.

REM --- 3. Install the app ----------------------------------------------
echo(
echo Installing the app... first time takes a few minutes, please wait.
if not exist ".venv\Scripts\python.exe" ( %PY% -m venv .venv )
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q
if errorlevel 1 (
  echo [X] Install failed. Check your internet connection and try again.
  pause
  exit /b 1
)
echo [OK] Installed.

REM --- 4. Ask for keys (all optional) ----------------------------------
echo(
echo ------------------------------------------------------------
echo  Your keys - press ENTER to skip any. Saved to a file (.env)
echo ------------------------------------------------------------
> .env echo # Shorts Clipper settings - re-run setup.bat to change these.

set "GEMINI="
set /p "GEMINI=Google Gemini key (free: aistudio.google.com/apikey): "
if not "!GEMINI!"=="" ( >> .env echo GEMINI_API_KEY=!GEMINI! )

set "PEX="
set /p "PEX=Pexels key for stock video (free: pexels.com/api): "
if not "!PEX!"=="" ( >> .env echo PEXELS_API_KEY=!PEX! )

set "YTID="
set /p "YTID=YouTube Client ID (for posting) : "
if not "!YTID!"=="" ( >> .env echo YOUTUBE_CLIENT_ID=!YTID! )

set "YTSEC="
set /p "YTSEC=YouTube Client Secret          : "
if not "!YTSEC!"=="" ( >> .env echo YOUTUBE_CLIENT_SECRET=!YTSEC! )

echo(
echo ============================================
echo    All done!
echo    Now double-click  run.bat  to start it.
echo ============================================
echo(
pause
