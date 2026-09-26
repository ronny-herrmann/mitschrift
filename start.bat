@echo off
REM Mitschrift – Start unter Windows (einmalige Installation beim ersten Aufruf)
cd /d "%~dp0"
where python >nul 2>nul || (echo Python fehlt: bitte https://www.python.org/downloads/ installieren ^(Haken "Add to PATH"^). & pause & exit /b 1)
if not exist .venv (
  echo Richte Umgebung ein ^(einmalig, 2-5 Minuten^) ...
  python -m venv .venv || (pause & exit /b 1)
  .venv\Scripts\python -m pip install --upgrade pip >nul
  .venv\Scripts\python -m pip install -r requirements.txt || (pause & exit /b 1)
)
if not exist .env copy .env.example .env >nul
echo Starte Mitschrift ... beim ersten Start wird das Modell geladen ^(~490 MB^).
start "" cmd /c "timeout /t 8 >nul & start http://localhost:8000"
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
