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

venv_is_usable() {
    # Python 3.11+ that runs and has pip. A Python upgrade that removed or replaced the
    # interpreter the venv links to leaves pip behind in the old version's site-packages
    .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1 &&
        .venv/bin/python -m pip --version >/dev/null 2>&1
}

has_google_chrome() {
    # The paths Playwright's channel="chrome" launches
    [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] || [ -x /opt/google/chrome/chrome ]
}

# Rebuild only an unusable venv; a half-installed one is kept and resumed
if ! venv_is_usable; then
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
    if ! .venv/bin/python -m pip install -r requirements.txt; then
        echo "Не удалось установить зависимости." >&2
        exit 1
    fi
    cp requirements.txt "$REQUIREMENTS_STAMP"
    # A new Playwright version may need a different Chromium build
    rm -f "$CHROMIUM_STAMP"
fi

# Sync prefers installed Google Chrome, so bundled Chromium is only a fallback;
# Playwright does not ship it for older macOS and some Linux distributions
if [ ! -f "$CHROMIUM_STAMP" ] && ! has_google_chrome; then
    if .venv/bin/playwright install chromium; then
        touch "$CHROMIUM_STAMP"
    else
        echo "Не удалось установить Chromium для Playwright. Для синхронизации установите Google Chrome." >&2
        echo "На Linux могут понадобиться системные библиотеки: sudo .venv/bin/playwright install-deps chromium" >&2
    fi
fi

# Otherwise the browser would open whatever already listens there while uvicorn fails to bind
if ! .venv/bin/python - "$HOST" "$PORT" <<'PY'
import socket
import sys

with socket.socket() as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((sys.argv[1], int(sys.argv[2])))
    except OSError:
        sys.exit(1)
PY
then
    echo "Порт $PORT уже занят: возможно, DigitalCheckAI уже запущен. Откройте $URL или остановите другой процесс." >&2
    exit 1
fi

wait_for_server() {
    # One Python process polls for up to 30 s and fails on timeout. Proxies are bypassed
    # and any HTTP response counts, so only a refused connection means "not started yet"
    .venv/bin/python - "http://$HOST:$PORT" <<'PY'
import sys
import time
import urllib.error
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(30):
    try:
        with opener.open(sys.argv[1], timeout=1):
            sys.exit(0)
    except urllib.error.HTTPError:
        sys.exit(0)
    except OSError:
        time.sleep(1)
sys.exit(1)
PY
}

open_browser() {
    # Wait for the server so the page does not open on a connection error
    if ! wait_for_server; then
        echo "Сервер не ответил за 30 секунд. Когда он запустится, откройте $URL в браузере." >&2
    elif command -v open >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then
        open "$URL"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1
    else
        echo "Откройте $URL в браузере."
    fi
}
open_browser &

exec .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
