#!/usr/bin/env bash
# One-command installer for a clean Termux (also works on Linux/macOS):
#   curl -fsSL https://raw.githubusercontent.com/Okimar/white-house/main/get.sh | bash
# Re-running updates an existing install and keeps your data.
#
# Everything is inside one function called on the last line: when the script comes through a pipe, bash
# reads it whole before running anything, so a child (pkg, git) that reads stdin cannot eat the rest of it.
set -euo pipefail

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mОшибка:\033[0m %s\n' "$*" >&2; exit 1; }

main() {
    local REPO="${WHITE_HOUSE_REPO:-https://github.com/Okimar/white-house.git}"
    local DIR="${WHITE_HOUSE_DIR:-$HOME/white-house}"
    local before after out

    if [ -n "${TERMUX_VERSION:-}" ] || [[ "${PREFIX:-}" == *com.termux* ]]; then
        say "Termux: ставлю git и python"
        pkg update -y </dev/null
        pkg install -y git python </dev/null
    fi
    command -v git >/dev/null 2>&1 || die "Нужен git. Установите его и повторите."

    if [ -d "$DIR/.git" ]; then
        say "Обновляю White House в $DIR"
        before="$(git -C "$DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
        # Older installs recorded start.sh as 100644 and install.sh made it executable: git then saw a "change"
        # and refused to pull. Ignore mode bits (content edits still count); restore them after the pull.
        git -C "$DIR" config core.fileMode false </dev/null >/dev/null 2>&1 || true
        if ! out="$(git -C "$DIR" pull --ff-only </dev/null 2>&1)"; then
            printf '%s\n' "$out" >&2
            local changed
            changed="$(git -C "$DIR" status --short --untracked-files=no </dev/null 2>/dev/null || true)"
            if [ -n "$changed" ]; then
                printf 'Изменены файлы программы:\n%s\n' "$changed" >&2
            fi
            die "не удалось обновить (причина выше). Скорее всего, в папке $DIR есть ваши правки файлов программы (список выше) или нет сети. Папка data/ не тронута. Что делать: проверьте интернет; если правки не нужны, верните файл командой: git -C \"$DIR\" checkout -- ИМЯ_ФАЙЛА  и запустите команду снова."
        fi
        chmod +x "$DIR/start.sh" "$DIR/install.sh" 2>/dev/null || true
        after="$(git -C "$DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
        if [ "$before" = "$after" ]; then
            say "Уже последняя версия: $after"
        else
            say "Было: $before, стало: $after"
        fi
    else
        say "Скачиваю White House в $DIR"
        git clone --depth 1 "$REPO" "$DIR" </dev/null || die "не удалось скачать $REPO. Проверьте интернет и повторите."
        say "Версия: $(git -C "$DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
    fi

    bash "$DIR/install.sh" </dev/null

    # A short command to start it from anywhere.
    local BIN="${PREFIX:-$HOME/.local}/bin"
    mkdir -p "$BIN"
    printf '#!/usr/bin/env bash\nexec "%s/start.sh" "$@"\n' "$DIR" > "$BIN/white-house"
    chmod +x "$BIN/white-house"

    # An old copy still running on this data folder keeps serving the old code: the page shows the old version.
    if [ -x "$DIR/.venv/bin/python" ] && \
       ! (cd "$DIR" && .venv/bin/python -c '
import os, sys
from pathlib import Path
from tavern.util.lock import DataDirLocked, DirLock
lock = DirLock(Path(os.environ.get("TAVERN_DATA_DIR") or "data").resolve() / ".lock")
try:
    lock.acquire()
except DataDirLocked:
    sys.exit(1)
lock.release()
' </dev/null >/dev/null 2>&1); then
        printf '\033[1;33mВнимание:\033[0m старая копия White House ещё запущена на этих данных, поэтому страница покажет СТАРУЮ версию.\n' >&2
        printf '    Остановите её: Ctrl+C в её окне, или командой  pkill -f "m tavern"\n' >&2
        printf '    Потом запустите: white-house\n' >&2
        say "Обновление установлено ($(git -C "$DIR" rev-parse --short HEAD 2>/dev/null || echo '?')), новая версия начнёт работать после перезапуска"
        return 0
    fi

    say "Готово! В следующий раз запуск: white-house (из любой папки)"
    say "Запускаю сейчас: здесь пойдёт лог, окно откроется в браузере. Остановить: Ctrl+C"
    # In a piped run stdin is the script's pipe, which the server must not read: give it the terminal
    # (so Ctrl+C and the log behave) or nothing.
    if [ ! -t 0 ] && { : </dev/tty; } 2>/dev/null; then
        exec "$DIR/start.sh" </dev/tty
    elif [ ! -t 0 ]; then
        exec "$DIR/start.sh" </dev/null
    fi
    exec "$DIR/start.sh"
}

main "$@"
