@echo off
chcp 65001 >nul
echo ===================================================
echo   Запуск DigitalCheckAI (http://localhost:8000)
echo ===================================================

if not exist ".venv\Scripts\python.exe" (
    echo Создание виртуального окружения .venv...
    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
    .venv\Scripts\playwright install chromium
)

start "" http://localhost:8000
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
