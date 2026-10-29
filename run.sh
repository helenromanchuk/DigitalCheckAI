#!/usr/bin/env bash
# Launcher for macOS and Linux; run.bat is the Windows equivalent.
set -euo pipefail
cd "$(dirname "$0")"

URL="http://localhost:8000"
echo "==================================================="
echo "  Запуск DigitalCheckAI ($URL)"
echo "==================================================="

find_python() {
    for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

if [ ! -x ".venv/bin/python" ]; then
    if ! PYTHON="$(find_python)"; then
        echo "Нужен Python 3.11+ (macOS: brew install python; Linux: пакет python3 и python3-venv)." >&2
        exit 1
    fi
    echo "Создание виртуального окружения .venv..."
    # Remove a half-built environment so the next run retries the installation
    if ! { "$PYTHON" -m venv .venv && .venv/bin/pip install -r requirements.txt; }; then
        rm -rf .venv
        echo "Не удалось установить зависимости." >&2
        exit 1
    fi
    # Sync prefers installed Google Chrome, so bundled Chromium is only a fallback;
    # Playwright does not ship it for older macOS and some Linux distributions
    if ! .venv/bin/playwright install chromium; then
        echo "Не удалось установить Chromium для Playwright. Для синхронизации установите Google Chrome." >&2
        echo "На Linux могут понадобиться системные библиотеки: sudo .venv/bin/playwright install-deps chromium" >&2
    fi
fi

open_browser() {
    # Wait for the server so the page does not open on a connection error
    for _ in $(seq 1 30); do
        if curl -s -o /dev/null "$URL" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    if command -v open >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then
        open "$URL"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1
    else
        echo "Откройте $URL в браузере."
    fi
}
open_browser &

exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
