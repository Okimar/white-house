#!/usr/bin/env bash
# Start Tavern. Arguments go to `python -m tavern`, e.g. ./start.sh --sandbox --port 9000
# The «Обновить» button on the page ends the app with exit code 75; this script then starts it again
# (after installing the libraries an update may have added). Everything is inside one function, which bash
# reads whole before running, so an update replacing this very file cannot confuse the run in progress.
set -euo pipefail
cd "$(dirname "$0")"

[ -x .venv/bin/python ] || { echo "Not installed yet. Run: bash install.sh" >&2; exit 1; }

# Termux: keep the CPU awake so Android does not suspend the server mid-reply.
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock
    trap 'termux-wake-unlock' EXIT
fi

main() {
    local code
    while true; do
        # Also after an update: the installed list is remembered and compared with requirements.txt.
        if ! cmp -s requirements.txt .venv/requirements.installed 2>/dev/null; then
            echo "==> Installing dependencies"
            .venv/bin/python -m pip install --disable-pip-version-check -q --only-binary=:all: -r requirements.txt || return 1
            cp requirements.txt .venv/requirements.installed
        fi
        code=0
        TAVERN_SUPERVISED=1 .venv/bin/python -m tavern "$@" || code=$?
        [ "$code" -eq 75 ] || return "$code"
        echo "==> Перезапуск после обновления"
        export TAVERN_RESTARTED=1
    done
}
main "$@"
exit $?
