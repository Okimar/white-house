#!/usr/bin/env bash
# One-shot installer for Linux, macOS and Termux (Android).
#   bash install.sh          install or update dependencies
#   ./start.sh               run Tavern
# Re-running is safe: it reuses the existing virtualenv.
set -euo pipefail

cd "$(dirname "$0")"
MIN_MINOR=11

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

is_termux() { [ -n "${TERMUX_VERSION:-}" ] || [[ "${PREFIX:-}" == *com.termux* ]]; }

if is_termux; then
    say "Termux detected"
    # Only prebuilt packages from the Termux repo; nothing is compiled.
    pkg update -y
    pkg install -y python
    command -v termux-open-url >/dev/null 2>&1 || say "Tip: 'pkg install termux-api' lets Tavern open the browser itself"
fi

PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || die "Python 3.$MIN_MINOR+ not found. Install it from your package manager."

"$PY" - "$MIN_MINOR" <<'EOF' || die "Python 3.$MIN_MINOR or newer is required."
import sys
sys.exit(0 if sys.version_info >= (3, int(sys.argv[1])) else 1)
EOF
say "Using $("$PY" --version)"

if [ ! -x .venv/bin/python ]; then
    say "Creating virtualenv in .venv"
    "$PY" -m venv .venv || die "venv failed. On Debian/Ubuntu: sudo apt install python3-venv"
fi

say "Installing dependencies (pure Python wheels, no compilation)"
# --only-binary: fail loudly instead of trying to build anything from source.
.venv/bin/python -m pip install --disable-pip-version-check -q --only-binary=:all: -r requirements.txt
cp requirements.txt .venv/requirements.installed  # start.sh compares it after an update

say "Checking installation"
.venv/bin/python -m tavern check

chmod +x start.sh
# Mode bits are not content: without this git reports start.sh as changed and refuses to update.
git config core.fileMode false >/dev/null 2>&1 || true
say "Done. Start with: ./start.sh"
