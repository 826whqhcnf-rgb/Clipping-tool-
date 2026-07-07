@echo off
REM ====================================================================
REM  Shorts Clipper - Windows launcher. Just DOUBLE-CLICK this file.
REM ====================================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo Please double-click  setup.bat  first.
  echo(
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"

echo(
echo ============================================
echo    Shorts Clipper is starting...
echo    Your browser will open in a few seconds.
echo    (If not, open:  http://localhost:8000 )
echo(
echo    Keep this window open while you use it.
echo    Close it when you're done.
echo ============================================
echo(

REM Open the browser a few seconds after the server starts.
start "Opening browser" cmd /c "ping 127.0.0.1 -n 6 >nul & start http://localhost:8000"

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

echo(
echo Server stopped.
pause
