@echo off
chcp 65001 >nul
rem Usage: run.bat       - local only (http://localhost:8000)
rem        run.bat lan   - also reachable from other computers on the local network

if not exist ".venv\Scripts\python.exe" (
    echo Создание виртуального окружения .venv...
    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
    .venv\Scripts\playwright install chromium
)

if /i "%~1"=="lan" goto lan

echo ===================================================
echo   Запуск DigitalCheckAI (http://localhost:8000)
echo ===================================================
start "" http://localhost:8000
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
goto :eof

:lan
echo ===================================================
echo   Запуск DigitalCheckAI для локальной сети
echo ===================================================
echo Откройте на другом компьютере один из адресов:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=*" %%b in ("%%a") do echo   http://%%b:8000
)
echo.
echo Если страница не открывается, разрешите порт 8000 в брандмауэре
echo (один раз, в командной строке от имени администратора):
echo   netsh advfirewall firewall add rule name="DigitalCheckAI" dir=in action=allow protocol=TCP localport=8000 profile=private
echo.
echo ВНИМАНИЕ: входа по паролю нет, приложение видно всем в этой сети.
echo Синхронизация открывает окно входа на ЭТОМ компьютере;
echo с других компьютеров используйте импорт JSON.
echo ===================================================
start "" http://localhost:8000
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
