# pycp — MCP-сервер для запуска Python-скриптов агентами

MCP-сервер (stdio), который даёт агентам OpenCode три инструмента: запуск `.py`-скрипта,
выполнение inline-кода и проверку интерпретатора. Флаги, позиционные аргументы и stdin-ввод
передаются структурно — агенту не нужно экранировать командную строку или городить shell.

> Скрипты выполняются **с полными правами пользователя, без песочницы**. Запускайте только то, что понимаете.

## Быстрая установка (одной командой)

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/rival-afk/pycp/main/install.ps1 | iex
```

Linux / macOS (sh):

```sh
curl -fsSL https://raw.githubusercontent.com/rival-afk/pycp/main/install.sh | sh
```

Что делают скрипты: клонируют (или обновляют) pycp в `%LOCALAPPDATA%\pycp` / `~/.local/share/pycp`,
ставят зависимости (`pip install -r requirements.txt`, при неудаче — venv) и регистрируют
MCP-сервер в OpenCode **глобально**. Флаг `--local` (`sh -s -- --local`) или `-Local` регистрирует
только в текущем проекте. Если `opencode` не найден в PATH — скрипт выведет JSON для ручной вставки.

Проверка: `opencode mcp list` → `pycp  connected`.

## Инструменты

| Инструмент | Описание |
|---|---|
| `run_python(script, args=[], flags={}, stdin="", timeout_seconds=60, cwd="", python="", env={}, max_output_chars=50000)` | Запустить `.py`-скрипт, вернуть exit code, stdout, stderr |
| `run_python_code(code, args=[], flags={}, stdin="", ...)` | Выполнить inline-код через `python -c` (аргументы попадают в `sys.argv[1:]`) |
| `python_info(python="")` | Какой интерпретатор используется и его версия |

Возвращаемый текст: командная строка запуска, exit code (или отметка о таймауте), время выполнения,
`cwd`, затем `stdout:` и `stderr:` (каждый обрезается до `max_output_chars`, по умолчанию 50000).

## Флаги (`flags`)

| Значение | Результат в argv |
|---|---|
| `true` | `--ключ` |
| `false` / `null` | флаг опущен |
| число / строка | `--ключ=значение` |
| список / кортеж | `--ключ=v1 --ключ=v2` (повтор флага) |

Ключ с ведущим дефисом (`"-v"`, `"--verbose"`) используется как есть, иначе подставляется `--`.
Значения с пробелами безопасны: shell не участвует, argv собирается напрямую.

По умолчанию выставляются `PYTHONUTF8=1` и `PYTHONIOENCODING=utf-8` (если не заданы) —
чтобы русский текст не превращался в кракозябры; перекрываются через `env`.

## Примеры вызовов агентом

```text
run_python(script="C:/tools/render.py",
           flags={"quality": "high", "denoise": true, "samples": 256},
           args=["scene.blend"],
           cwd="C:/renders",
           timeout_seconds=600)

run_python(script="examples/demo_flags.py",
           flags={"name": "мир", "verbose": true, "count": 3},
           args=["pos1"],
           stdin="данные из stdin")

run_python_code(code="import sys; print(sys.argv[1:])",
                args=["a", "b"], flags={"x": 1})
```

## Ручная установка

```powershell
# зависимости
python -m pip install -r requirements.txt

# глобально (все проекты)
opencode mcp add pycp --global -- python "C:\path\to\pycp\server.py"

# локально (только текущий проект)
opencode mcp add pycp -- python "C:\path\to\pycp\server.py"
```

Либо добавить в `opencode.json(c)` вручную:

```json
{
  "mcp": {
    "servers": {
      "pycp": {
        "type": "local",
        "command": ["python", "C:/path/to/pycp/server.py"],
        "cwd": "C:/path/to/pycp"
      }
    }
  }
}
```

## Переменные окружения

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `PYCP_PYTHON` | Интерпретатор для запуска скриптов | `python` из PATH |
| `PYCP_TIMEOUT` | Таймаут по умолчанию, сек | `60` |
| `PYCP_MAX_TIMEOUT` | Максимально допустимый таймаут, сек | `600` |
| `PYCP_MAX_OUTPUT` | Лимит символов stdout/stderr | `50000` |

## Замечания

- `cwd`: если не задан, скрипт выполняется в папке скрипта; относительный путь скрипта резолвится от `cwd`.
- При таймауте процесс убивается; возвращаются уже накопленный вывод и отметка о таймауте.
- Скрипты получают те же права, что и пользователь: никакой изоляции нет.
