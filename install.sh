#!/bin/sh
# pycp installer: clones/updates pycp, installs deps, registers MCP server in OpenCode.
# Usage:  curl -fsSL https://raw.githubusercontent.com/rival-afk/pycp/main/install.sh | sh
#         with --local:  curl -fsSL .../install.sh | sh -s -- --local   (project instead of global)
set -eu

REPO_URL="https://github.com/rival-afk/pycp.git"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/pycp"
LOCAL=0
if [ "${1:-}" = "--local" ]; then LOCAL=1; fi

echo "pycp: установка в $DEST"

# 1) clone or update
if [ -d "$DEST/.git" ]; then
    echo "pycp: обновляю существующую установку..."
    git -C "$DEST" pull --ff-only
elif [ -e "$DEST" ]; then
    echo "pycp: ошибка: $DEST существует и не является git-репозиторием. Удалите её вручную." >&2
    exit 1
else
    git clone --depth 1 "$REPO_URL" "$DEST"
fi

# 2) interpreter
PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
    echo "pycp: ошибка: python3 не найден в PATH." >&2
    exit 1
fi
echo "pycp: интерпретатор: $PY"

# 3) deps (fallback: venv, e.g. PEP 668 externally-managed)
if ! "$PY" -m pip install -q -r "$DEST/requirements.txt"; then
    echo "pycp: pip install не удался, создаю venv в $DEST/.venv ..."
    "$PY" -m venv "$DEST/.venv"
    PY="$DEST/.venv/bin/python"
    "$PY" -m pip install -q -r "$DEST/requirements.txt"
fi
PY_EXE="$("$PY" -c 'import sys; print(sys.executable)')"
echo "pycp: python: $PY_EXE"

# 4) register in OpenCode
PYCP_SERVER="$DEST/server.py"
REGISTERED=0
if command -v opencode >/dev/null 2>&1; then
    if [ "$LOCAL" = "1" ]; then
        echo "pycp: регистрирую MCP-сервер в OpenCode (проект)..."
        opencode mcp remove pycp >/dev/null 2>&1 || true
        if opencode mcp add pycp -- "$PY_EXE" "$PYCP_SERVER"; then REGISTERED=1; fi
    else
        echo "pycp: регистрирую MCP-сервер в OpenCode (глобально)..."
        opencode mcp remove pycp >/dev/null 2>&1 || true
        if opencode mcp add pycp --global -- "$PY_EXE" "$PYCP_SERVER"; then REGISTERED=1; fi
    fi
    if [ "$REGISTERED" = "1" ]; then
        echo "pycp: готово. Проверка: opencode mcp list  (ожидается 'pycp  connected')"
    else
        echo "pycp: 'opencode mcp add' завершилось с ошибкой. Пробую записать конфиг напрямую..." >&2
    fi
fi

if [ "$REGISTERED" != "1" ]; then
    # CLI может отсутствовать (например, установлено только десктопное приложение).
    echo "pycp: ищу конфиг OpenCode, чтобы зарегистрироваться без CLI..."
    if [ "$LOCAL" = "1" ]; then
        CONFIG_DIRS="$PWD"
    else
        CONFIG_DIRS="${XDG_CONFIG_HOME:-$HOME/.config}/opencode $HOME/.config/opencode"
    fi
    MERGE_SCRIPT="$DEST/merge_mcp_config.py"
    for dir in $CONFIG_DIRS; do
        for cfg in "$dir/opencode.jsonc" "$dir/opencode.json"; do
            [ -f "$cfg" ] || continue
            [ -f "$MERGE_SCRIPT" ] || break 2
            if "$PY" "$MERGE_SCRIPT" "$cfg" "$PY_EXE" "$PYCP_SERVER" "$DEST"; then
                REGISTERED=1
                echo "pycp: готово. Перезапустите OpenCode, чтобы подключить инструменты pycp."
                break 2
            fi
            echo "pycp: не удалось обновить конфиг $cfg" >&2
        done
    done
fi

if [ "$REGISTERED" != "1" ]; then
    echo "pycp: НЕ зарегистрирован — opencode CLI и подходящий конфиг не найдены (или запись не удалась)." >&2
    echo "pycp: добавьте в opencode.json(c) вручную:" >&2
    cat <<EOF
{
  "mcp": {
    "servers": {
      "pycp": {
        "type": "local",
        "command": ["$PY_EXE", "$PYCP_SERVER"],
        "cwd": "$DEST"
      }
    }
  }
}
EOF
    exit 1
fi
