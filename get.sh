#!/usr/bin/env bash
# One-command installer for a clean Termux (also works on Linux/macOS):
#   curl -fsSL https://raw.githubusercontent.com/Okimar/white-house/main/get.sh | bash
# Re-running updates an existing install and keeps your data.
set -euo pipefail

REPO="${WHITE_HOUSE_REPO:-https://github.com/Okimar/white-house.git}"
DIR="${WHITE_HOUSE_DIR:-$HOME/white-house}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mОшибка:\033[0m %s\n' "$*" >&2; exit 1; }

if [ -n "${TERMUX_VERSION:-}" ] || [[ "${PREFIX:-}" == *com.termux* ]]; then
    say "Termux: ставлю git и python"
    pkg update -y
    pkg install -y git python
fi
command -v git >/dev/null 2>&1 || die "Нужен git. Установите его и повторите."

if [ -d "$DIR/.git" ]; then
    say "Обновляю White House в $DIR"
    git -C "$DIR" pull --ff-only
else
    say "Скачиваю White House в $DIR"
    git clone --depth 1 "$REPO" "$DIR"
fi

bash "$DIR/install.sh"

# A short command to start it from anywhere.
BIN="${PREFIX:-$HOME/.local}/bin"
mkdir -p "$BIN"
printf '#!/usr/bin/env bash\nexec "%s/start.sh" "$@"\n' "$DIR" > "$BIN/white-house"
chmod +x "$BIN/white-house"

say "Готово! В следующий раз запуск: white-house (из любой папки)"
say "Запускаю сейчас: здесь пойдёт лог, окно откроется в браузере. Остановить: Ctrl+C"
exec "$DIR/start.sh"
