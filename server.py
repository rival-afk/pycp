"""MCP-сервер pycp: запуск python-скриптов и кода с флагами и stdin-вводом.

Инструменты:
  run_python       — запустить .py-скрипт (флаги через flags, аргументы через args)
  run_python_code  — выполнить inline-код через python -c
  python_info      — какой интерпретатор будет использоваться и его версия
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Optional

from mcp.server.mcpserver import MCPServer

DEFAULT_PYTHON = os.environ.get("PYCP_PYTHON", "python")
DEFAULT_TIMEOUT = int(os.environ.get("PYCP_TIMEOUT", "60"))
MAX_TIMEOUT = int(os.environ.get("PYCP_MAX_TIMEOUT", "600"))
MAX_OUTPUT = int(os.environ.get("PYCP_MAX_OUTPUT", "50000"))

mcp = MCPServer(
    "pycp",
    instructions=(
        "Запускает python-скрипты и python-код от имени пользователя (полные права, без песочницы). "
        "run_python — файл скрипта: позиционные аргументы в args, флаги в flags, ввод в stdin. "
        "run_python_code — inline-код (python -c). python_info — какой интерпретатор используется. "
        "Флаги: true -> голый флаг (--ключ), false/null -> флаг опущен, число/строка -> --ключ=значение, "
        "список/кортеж -> флаг повторяется (--ключ=v1 --ключ=v2). Ключи с ведущими дефисами используются как есть, "
        "иначе перед ключом добавляется --. Значения с пробелами безопасны: шелл не используется. "
        "cwd: если не задан, скрипт выполняется в папке скрипта (для run_python_code — в cwd сервера); "
        "относительные пути скрипта резолвятся от cwd, либо от cwd сервера. "
        "Вывод stdout/stderr обрезается до max_output_chars. Не запускайте то, что не понимаете."
    ),
)


def build_flags(flags: Optional[dict]) -> list[str]:
    """Сериализует flags в argv.

    True -> --key; False/None -> опущен; скаляр -> --key=value;
    список/кортеж -> --key=v повторяется. Ключи с ведущим '-' — как есть.
    """
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


def resolve_script(script: str, cwd: str) -> str:
    path = os.path.expandvars(os.path.expanduser(script.strip()))
    if not os.path.isabs(path):
        base = cwd if cwd else os.getcwd()
        path = os.path.join(base, path)
    return os.path.normpath(os.path.abspath(path))


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


@mcp.tool()
async def run_python(
    script: str,
    args: Optional[list[str]] = None,
    flags: Optional[dict] = None,
    stdin: str = "",
    timeout_seconds: int = DEFAULT_TIMEOUT,
    cwd: str = "",
    python: str = "",
    env: Optional[dict] = None,
    max_output_chars: int = MAX_OUTPUT,
) -> str:
    """Запускает .py-скрипт и возвращает exit code, stdout и stderr.

    Args:
        script: Путь к скрипту (абсолютный или относительно cwd).
        args: Позиционные аргументы скрипта, например ["input.txt", "output.txt"].
        flags: Флаги в виде объекта: true -> --ключ, false/null -> опущен,
            число/строка -> --ключ=значение, список -> повтор флага.
            Ключ с ведущим '-' (например "-v" или "--verbose") используется как есть.
        stdin: Текст, который будет передан скрипту в стандартный ввод.
        timeout_seconds: Таймаут в секундах (по умолчанию 60, максимум 600). При таймауте процесс убивается.
        cwd: Рабочая директория. Если пусто — папка скрипта. Отсюда же резолвится относительный путь скрипта.
        python: Интерпретатор (например "python", "python3", абсолютный путь к venv).
            По умолчанию — переменная PYCP_PYTHON или "python" из PATH.
        env: Дополнительные переменные окружения для процесса (перекрывают текущие).
            По умолчанию выставляются PYTHONUTF8=1 и PYTHONIOENCODING=utf-8 (если не заданы).
        max_output_chars: Лимит символов на stdout и stderr по отдельности (по умолчанию 50000).
    """
    if not script or not script.strip():
        return "ошибка: параметр script пуст."
    cwd_param = cwd.strip() if cwd else ""
    if cwd_param:
        cwd_param = os.path.normpath(os.path.abspath(os.path.expandvars(os.path.expanduser(cwd_param))))
        if not os.path.isdir(cwd_param):
            return f"ошибка: cwd не существует или не директория: {cwd_param}"
    script_abs = resolve_script(script, cwd_param)
    if not os.path.isfile(script_abs):
        return f"ошибка: скрипт не найден: {script_abs}"
    work_dir = cwd_param if cwd_param else os.path.dirname(script_abs)

    argv = [resolve_interpreter(python), script_abs]
    argv += build_flags(flags)
    argv += [str(a) for a in (args or [])]

    return await run_captured(
        argv,
        stdin,
        work_dir,
        child_env(env),
        timeout_seconds,
        max_output_chars,
    )


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
    """Выполняет inline-код через `python -c` и возвращает exit code, stdout и stderr.

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
    work_dir = cwd.strip() if cwd else os.getcwd()
    work_dir = os.path.normpath(os.path.abspath(os.path.expandvars(os.path.expanduser(work_dir))))
    if not os.path.isdir(work_dir):
        return f"ошибка: cwd не существует или не директория: {work_dir}"

    argv = [resolve_interpreter(python), "-c", code]
    argv += build_flags(flags)
    argv += [str(a) for a in (args or [])]

    return await run_captured(
        argv,
        stdin,
        work_dir,
        child_env(env),
        timeout_seconds,
        max_output_chars,
    )


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
        f"{info}"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
