#!/usr/bin/env bash
# Launcher for macOS and Linux; run.bat is the Windows equivalent.
set -euo pipefail
cd "$(dirname "$0")"

HOST="127.0.0.1"
PORT="8000"
URL="http://localhost:$PORT"
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

# Rebuild only when the interpreter does not run, e.g. after a Homebrew Python upgrade
# removed the one the venv links to; a half-installed venv is kept and resumed
if ! .venv/bin/python -c '' >/dev/null 2>&1; then
    if ! PYTHON="$(find_python)"; then
        echo "Нужен Python 3.11+ (macOS: brew install python; Linux: пакет python3 и python3-venv)." >&2
        exit 1
    fi
    echo "Создание виртуального окружения .venv..."
    if ! "$PYTHON" -m venv --clear .venv; then
        rm -rf .venv
        echo "Не удалось создать .venv. На Debian и Ubuntu установите пакет python3-venv." >&2
        exit 1
    fi
fi

# Each stamp is written only after its step succeeds, so a failed or interrupted
# step, or a changed requirements.txt, runs again on the next launch
REQUIREMENTS_STAMP=".venv/requirements.installed"
CHROMIUM_STAMP=".venv/chromium.installed"

if ! cmp -s requirements.txt "$REQUIREMENTS_STAMP"; then
    echo "Установка зависимостей..."
    if ! .venv/bin/pip install -r requirements.txt; then
        echo "Не удалось установить зависимости." >&2
        exit 1
    fi
    cp requirements.txt "$REQUIREMENTS_STAMP"
    # A new Playwright version may need a different Chromium build
    rm -f "$CHROMIUM_STAMP"
fi

if [ ! -f "$CHROMIUM_STAMP" ]; then
    # Sync prefers installed Google Chrome, so bundled Chromium is only a fallback;
    # Playwright does not ship it for older macOS and some Linux distributions
    if .venv/bin/playwright install chromium; then
        touch "$CHROMIUM_STAMP"
    else
        echo "Не удалось установить Chromium для Playwright. Для синхронизации установите Google Chrome." >&2
        echo "На Linux могут понадобиться системные библиотеки: sudo .venv/bin/playwright install-deps chromium" >&2
    fi
fi

wait_for_server() {
    # One Python process polls for up to 30 s. Proxies are bypassed and any HTTP
    # response counts, so only a refused connection means "not started yet"
    .venv/bin/python - "http://$HOST:$PORT" <<'PY'
import sys
import time
import urllib.error
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(30):
    try:
        opener.open(sys.argv[1], timeout=1)
        break
    except urllib.error.HTTPError:
        break
    except OSError:
        time.sleep(1)
PY
}

open_browser() {
    # Wait for the server so the page does not open on a connection error
    wait_for_server
    if command -v open >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then
        open "$URL"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1
    else
        echo "Откройте $URL в браузере."
    fi
}
open_browser &

exec .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
