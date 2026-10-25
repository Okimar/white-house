#!/usr/bin/env bash
# Start Tavern. Arguments go to `python -m tavern`, e.g. ./start.sh --sandbox --port 9000
set -euo pipefail
cd "$(dirname "$0")"

[ -x .venv/bin/python ] || { echo "Not installed yet. Run: bash install.sh" >&2; exit 1; }

# Termux: keep the CPU awake so Android does not suspend the server mid-reply.
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock
    trap 'termux-wake-unlock' EXIT
fi

.venv/bin/python -m tavern "$@"
