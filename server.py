"""MCP-сервер pycp: агенты создают и запускают временные python-скрипты.

Ключевые правила:
  - вызывать существующие (созданные вне pycp) скрипты НЕЛЬЗЯ — только управляемые pycp;
  - управляемый скрипт удаляется через SCRIPT_TTL секунд после последнего использования;
  - любой вызов (создание/запуск) сбрасывает таймер удаления;
  - расписание удалений хранится в state.json и переживает перезапуск сервера.

Инструменты:
  write_python_script  — создать/перезаписать управляемый скрипт
  run_python           — запустить управляемый скрипт по имени
  run_python_code      — выполнить inline-код (python -c), файла не создаёт
  list_python_scripts  — список управляемых скриптов и остатка до удаления
  delete_python_script — удалить скрипт немедленно
  python_info          — какой интерпретатор будет использоваться
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import threading
import time
from typing import Optional

from mcp.server.mcpserver import MCPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.normpath(
    os.path.abspath(os.path.expandvars(os.path.expanduser(os.environ.get("PYCP_SCRIPTS_DIR", os.path.join(BASE_DIR, "scripts")))))
)
STATE_PATH = os.path.normpath(
    os.path.abspath(os.path.expandvars(os.path.expanduser(os.environ.get("PYCP_STATE", os.path.join(BASE_DIR, "state.json")))))
)

DEFAULT_PYTHON = os.environ.get("PYCP_PYTHON", "python")
DEFAULT_TIMEOUT = int(os.environ.get("PYCP_TIMEOUT", "60"))
MAX_TIMEOUT = int(os.environ.get("PYCP_MAX_TIMEOUT", "600"))
MAX_OUTPUT = int(os.environ.get("PYCP_MAX_OUTPUT", "50000"))
SCRIPT_TTL = int(os.environ.get("PYCP_SCRIPT_TTL", str(5 * 60)))
SWEEP_INTERVAL = int(os.environ.get("PYCP_SWEEP_INTERVAL", "15"))

NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$")
_lock = threading.RLock()

mcp = MCPServer(
    "pycp",
    instructions=(
        "Запускает python-код от имени пользователя (полные права, без песочницы). "
        "Существующие скрипты вызывать НЕЛЬЗЯ: run_python запускает только скрипты, созданные через write_python_script. "
        "Управляемый скрипт автоматически удаляется через 5 минут после последнего использования "
        "(любой вызов сбрасывает таймер); расписание хранится в state.json и переживает перезапуск. "
        "run_python_code — inline-код (файл не создаётся). python_info — какой интерпретатор используется. "
        "Флаги: true -> голый флаг (--ключ), false/null -> флаг опущен, число/строка -> --ключ=значение, "
        "список/кортеж -> флаг повторяется. Ключи с ведущими дефисами используются как есть, иначе --. "
        "cwd: если не задан, процесс выполняется в cwd сервера (обычно корень проекта). "
        "Вывод stdout/stderr обрезается до max_output_chars. Не запускайте то, что не понимаете."
    ),
)


# ---------- состояние и удаление по таймеру ----------

def load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("scripts"), dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"scripts": {}}


def save_state(state: dict) -> None:
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, STATE_PATH)


def script_path(name: str) -> str:
    filename = name if name.endswith(".py") else name + ".py"
    return os.path.join(SCRIPTS_DIR, filename)


def normalize_name(name: str) -> Optional[str]:
    value = (name or "").strip()
    if value.endswith(".py"):
        value = value[:-3]
    if not NAME_RE.match(value):
        return None
    return value


def _sweep_locked(state: dict) -> list[str]:
    """Удаляет просроченные скрипты (и осиротевшие файлы в managed-папке). Вызывать под _lock."""
    now = time.time()
    expired: list[str] = []
    for name, entry in list(state["scripts"].items()):
        last_used = float(entry.get("last_used", 0))
        if now - last_used > SCRIPT_TTL:
            path = entry.get("path") or script_path(name)
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                pass  # оставляем запись — попробуем снова при следующем проходе
            else:
                state["scripts"].pop(name, None)
                expired.append(name)
                continue
            # файл не удалился: повторный шанс пропустим, но запись не держим вечно
            state["scripts"].pop(name, None)
            expired.append(name)
    # файлы в managed-папке без записи в state — тоже созданы pycp, удаляем
    try:
        for filename in os.listdir(SCRIPTS_DIR):
            if not filename.endswith(".py"):
                continue
            full = os.path.join(SCRIPTS_DIR, filename)
            if not any(os.path.normpath(e.get("path", "")) == os.path.normpath(full) for e in state["scripts"].values()):
                try:
                    os.remove(full)
                    expired.append(filename[:-3])
                except OSError:
                    pass
    except FileNotFoundError:
        pass
    return expired


def sweep() -> list[str]:
    with _lock:
        os.makedirs(SCRIPTS_DIR, exist_ok=True)
        state = load_state()
        expired = _sweep_locked(state)
        if expired:
            save_state(state)
        return expired


def touch_script_locked(state: dict, name: str, path: str) -> None:
    now = time.time()
    entry = state["scripts"].get(name, {})
    entry.setdefault("created", now)
    entry["path"] = path
    entry["last_used"] = now
    state["scripts"][name] = entry


def _sweeper_loop() -> None:
    while True:
        time.sleep(SWEEP_INTERVAL)
        try:
            sweep()
        except Exception:
            pass  # свипер не должен умирать


# стартовое подметание: доудаляем просроченное, пережившее прошлый запуск
with _lock:
    try:
        os.makedirs(SCRIPTS_DIR, exist_ok=True)
        _st = load_state()
        if _sweep_locked(_st):
            save_state(_st)
    except OSError:
        pass
threading.Thread(target=_sweeper_loop, daemon=True, name="pycp-sweeper").start()


# ---------- запуск процессов ----------

def build_flags(flags: Optional[dict]) -> list[str]:
    argv: list[str] = []
    for key, value in (flags or {}).items():
        name = str(key)
        prefix = name if name.startswith("-") else f"--{name}"
        if value is None or value is False:
            continue
        if value is True:
            argv.append(prefix)
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                argv.append(f"{prefix}={item}")
            continue
        argv.append(f"{prefix}={value}")
    return argv


def cmdline(argv: list[str]) -> str:
    out: list[str] = []
    for part in argv:
        if any(ch.isspace() for ch in part) or any(ch in part for ch in '"\''):
            out.append('"' + part.replace('"', '\\"') + '"')
        else:
            out.append(part)
    return " ".join(out)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[обрезано {len(text) - limit} символов]"


def child_env(env: Optional[dict]) -> dict:
    merged = os.environ.copy()
    merged.setdefault("PYTHONUTF8", "1")
    merged.setdefault("PYTHONIOENCODING", "utf-8")
    for key, value in (env or {}).items():
        merged[str(key)] = str(value)
    return merged


def clamp_timeout(timeout_seconds: int) -> int:
    try:
        value = int(timeout_seconds)
    except (TypeError, ValueError):
        value = DEFAULT_TIMEOUT
    return max(1, min(value, MAX_TIMEOUT))


def clamp_output(max_output_chars: int) -> int:
    try:
        value = int(max_output_chars)
    except (TypeError, ValueError):
        value = MAX_OUTPUT
    return max(100, min(value, 1_000_000))


async def run_captured(
    argv: list[str],
    stdin_text: str,
    cwd: str,
    env: dict,
    timeout_seconds: int,
    max_output_chars: int,
) -> str:
    limit = clamp_output(max_output_chars)
    timeout = clamp_timeout(timeout_seconds)
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            limit=1024 * 1024,
        )
    except FileNotFoundError:
        return (
            f"$ {cmdline(argv)}\n"
            f"ошибка: не найден интерпретатор «{argv[0]}». "
            f"Задайте другой через параметр python или переменную PYCP_PYTHON."
        )
    except OSError as exc:
        return f"$ {cmdline(argv)}\nошибка запуска: {exc}"

    timed_out = False
    try:
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(stdin_text.encode("utf-8") if stdin_text else None),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        out_b, err_b = await proc.communicate()
    duration = time.monotonic() - start

    stdout = truncate(out_b.decode("utf-8", errors="replace"), limit)
    stderr = truncate(err_b.decode("utf-8", errors="replace"), limit)

    exit_code = proc.returncode
    status = f"exit code: {exit_code}" if not timed_out else f"ТАЙМАУТ ({timeout}s), процесс убит"
    parts = [
        f"$ {cmdline(argv)}",
        f"{status}, время: {duration:.2f}s, cwd: {cwd}",
        "",
        "stdout:",
        stdout if stdout.strip() else "(пусто)",
        "",
        "stderr:",
        stderr if stderr.strip() else "(пусто)",
    ]
    return "\n".join(parts)


def resolve_interpreter(python: str) -> str:
    return python.strip() if python and python.strip() else DEFAULT_PYTHON


def resolve_cwd(cwd: str) -> tuple[Optional[str], Optional[str]]:
    """Возвращает (cwd, error). Пустой cwd -> cwd сервера."""
    work_dir = cwd.strip() if cwd else os.getcwd()
    work_dir = os.path.normpath(os.path.abspath(os.path.expandvars(os.path.expanduser(work_dir))))
    if not os.path.isdir(work_dir):
        return None, f"ошибка: cwd не существует или не директория: {work_dir}"
    return work_dir, None


# ---------- инструменты ----------

@mcp.tool()
def write_python_script(name: str, code: str) -> str:
    """Создаёт или перезаписывает управляемый python-скрипт (единственный способ добавить скрипт в pycp).

    Файл хранится в управляемой папке pycp и автоматически удаляется через 5 минут
    после последнего использования (любой вызов сбрасывает таймер).

    Args:
        name: Имя скрипта: латиница/цифры/_/-/., до 64 символов, без путей («render» или «render.py»).
        code: Полный текст python-скрипта.
    """
    norm = normalize_name(name)
    if norm is None:
        return "ошибка: недопустимое имя. Только буквы, цифры, '_', '-', '.', без разделителей путей, до 64 символов."
    if not code or not code.strip():
        return "ошибка: параметр code пуст."
    with _lock:
        sweep()  # подчистить просроченное перед созданием
        os.makedirs(SCRIPTS_DIR, exist_ok=True)
        path = script_path(norm)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(code)
        os.replace(tmp_path, path)
        state = load_state()
        touch_script_locked(state, norm, path)
        save_state(state)
    return (
        f"скрипт создан: {norm}\n"
        f"путь: {path}\n"
        f"автоудаление: через {SCRIPT_TTL}s после последнего использования (сбрасывается любым вызовом)"
    )


@mcp.tool()
async def run_python(
    name: str,
    args: Optional[list[str]] = None,
    flags: Optional[dict] = None,
    stdin: str = "",
    timeout_seconds: int = DEFAULT_TIMEOUT,
    cwd: str = "",
    python: str = "",
    env: Optional[dict] = None,
    max_output_chars: int = MAX_OUTPUT,
) -> str:
    """Запускает управляемый (созданный через write_python_script) скрипт по имени.

    Существующие файлы, созданные вне pycp, вызывать нельзя — передайте code в write_python_script.
    Каждый успешный запуск сбрасывает таймер автоудаления.

    Args:
        name: Имя управляемого скрипта (то, что передано в write_python_script).
        args: Позиционные аргументы скрипта.
        flags: Флаги: true -> --ключ, false/null -> опущен, число/строка -> --ключ=значение,
            список -> повтор флага. Ключ с ведущим '-' используется как есть.
        stdin: Текст для стандартного ввода.
        timeout_seconds: Таймаут в секундах (по умолчанию 60, максимум 600).
        cwd: Рабочая директория (по умолчанию — cwd сервера, обычно корень проекта).
        python: Интерпретатор (по умолчанию — PYCP_PYTHON или "python" из PATH).
        env: Дополнительные переменные окружения (по умолчанию PYTHONUTF8=1, PYTHONIOENCODING=utf-8).
        max_output_chars: Лимит символов на stdout и stderr по отдельности (по умолчанию 50000).
    """
    norm = normalize_name(name)
    if norm is None:
        return "ошибка: недопустимое имя скрипта."
    with _lock:
        sweep()
        state = load_state()
        entry = state["scripts"].get(norm)
    if entry is None:
        return (
            f"ошибка: скрипт «{norm}» не найден среди управляемых. "
            "pycp не запускает существующие файлы — создайте скрипт через write_python_script."
        )
    path = entry.get("path") or script_path(norm)
    if not os.path.isfile(path) or os.path.dirname(os.path.normpath(path)) != os.path.normpath(SCRIPTS_DIR):
        return f"ошибка: управляемый скрипт «{norm}» повреждён или вне managed-папки. Создайте его заново."
    work_dir, err = resolve_cwd(cwd)
    if err:
        return err

    argv = [resolve_interpreter(python), path]
    argv += build_flags(flags)
    argv += [str(a) for a in (args or [])]

    result = await run_captured(argv, stdin, work_dir, child_env(env), timeout_seconds, max_output_chars)
    with _lock:
        state = load_state()
        if norm in state["scripts"]:
            touch_script_locked(state, norm, path)
            save_state(state)
    return result


@mcp.tool()
async def run_python_code(
    code: str,
    args: Optional[list[str]] = None,
    flags: Optional[dict] = None,
    stdin: str = "",
    timeout_seconds: int = DEFAULT_TIMEOUT,
    cwd: str = "",
    python: str = "",
    env: Optional[dict] = None,
    max_output_chars: int = MAX_OUTPUT,
) -> str:
    """Выполняет inline-код через `python -c` (файл не создаётся, удалять нечего).

    Аргументы args и флаги flags попадают в sys.argv[1:] (sys.argv[0] = "-c").

    Args:
        code: Код на python, например "import sys; print(sys.argv[1:])".
        args: Позиционные аргументы (sys.argv[1:]).
        flags: Флаги в том же формате, что и у run_python.
        stdin: Текст для стандартного ввода.
        timeout_seconds: Таймаут в секундах (по умолчанию 60, максимум 600).
        cwd: Рабочая директория (по умолчанию — cwd сервера).
        python: Интерпретатор (по умолчанию — PYCP_PYTHON или "python" из PATH).
        env: Дополнительные переменные окружения для процесса.
        max_output_chars: Лимит символов на stdout и stderr по отдельности (по умолчанию 50000).
    """
    if not code or not code.strip():
        return "ошибка: параметр code пуст."
    with _lock:
        sweep()
    work_dir, err = resolve_cwd(cwd)
    if err:
        return err

    argv = [resolve_interpreter(python), "-c", code]
    argv += build_flags(flags)
    argv += [str(a) for a in (args or [])]

    return await run_captured(argv, stdin, work_dir, child_env(env), timeout_seconds, max_output_chars)


@mcp.tool()
def list_python_scripts() -> str:
    """Показывает управляемые скрипты и сколько секунд осталось до автоудаления каждого."""
    with _lock:
        sweep()
        state = load_state()
    if not state["scripts"]:
        return f"управляемых скриптов нет (managed-папка: {SCRIPTS_DIR})"
    now = time.time()
    lines = [f"managed-папка: {SCRIPTS_DIR}"]
    for name, entry in sorted(state["scripts"].items()):
        last_used = float(entry.get("last_used", 0))
        ttl_left = int(SCRIPT_TTL - (now - last_used))
        path = entry.get("path", "?")
        exists = "ok" if os.path.isfile(path) else "ФАЙЛ ОТСУТСТВУЕТ"
        lines.append(f"- {name}: удаление через {max(ttl_left, 0)}s, файл: {exists}, путь: {path}")
    return "\n".join(lines)


@mcp.tool()
def delete_python_script(name: str) -> str:
    """Немедленно удаляет управляемый скрипт и его запись в расписании.

    Args:
        name: Имя управляемого скрипта.
    """
    norm = normalize_name(name)
    if norm is None:
        return "ошибка: недопустимое имя скрипта."
    with _lock:
        state = load_state()
        entry = state["scripts"].pop(norm, None)
        if entry is None:
            return f"скрипт «{norm}» не найден среди управляемых."
        path = entry.get("path") or script_path(norm)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            state["scripts"][norm] = entry
            save_state(state)
            return f"ошибка удаления {path}: {exc}"
        save_state(state)
    return f"скрипт «{norm}» удалён: {path}"


@mcp.tool()
async def python_info(python: str = "") -> str:
    """Показывает, какой интерпретатор используется и его версию.

    Args:
        python: Интерпретатор для проверки (по умолчанию — PYCP_PYTHON или "python" из PATH).
    """
    interpreter = resolve_interpreter(python)
    code = (
        "import json, sys; print(json.dumps("
        "{'executable': sys.executable, 'version': sys.version.split()[0], "
        "'platform': sys.platform, 'prefix': sys.prefix}, ensure_ascii=False))"
    )
    argv = [interpreter, "-c", code]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024,
        )
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=15)
    except FileNotFoundError:
        return f"интерпретатор не найден: {interpreter}"
    except (asyncio.TimeoutError, OSError) as exc:
        return f"ошибка запуска {interpreter}: {exc}"
    if proc.returncode != 0:
        return f"$ {cmdline(argv)}\nexit code: {proc.returncode}\n{err_b.decode('utf-8', errors='replace')}"
    info = out_b.decode("utf-8", errors="replace").strip()
    return (
        f"интерпретатор по умолчанию (PYCP_PYTHON или PATH): {DEFAULT_PYTHON}\n"
        f"запрошено: {interpreter}\n"
        f"managed-папка: {SCRIPTS_DIR}\n"
        f"TTL автоудаления: {SCRIPT_TTL}s\n"
        f"{info}"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
