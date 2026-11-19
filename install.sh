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
if command -v opencode >/dev/null 2>&1; then
    if [ "$LOCAL" = "1" ]; then
        echo "pycp: регистрирую MCP-сервер в OpenCode (проект)..."
        opencode mcp remove pycp >/dev/null 2>&1 || true
        opencode mcp add pycp -- "$PY_EXE" "$DEST/server.py"
    else
        echo "pycp: регистрирую MCP-сервер в OpenCode (глобально)..."
        opencode mcp remove pycp >/dev/null 2>&1 || true
        opencode mcp add pycp --global -- "$PY_EXE" "$DEST/server.py"
    fi
    echo "pycp: готово. Проверка: opencode mcp list  (ожидается 'pycp  connected')"
else
    echo "pycp: opencode не найден в PATH. Добавьте в opencode.json вручную:"
    cat <<EOF
{
  "mcp": {
    "servers": {
      "pycp": {
        "type": "local",
        "command": ["$PY_EXE", "$DEST/server.py"]
      }
    }
  }
}
EOF
fi
