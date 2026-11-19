# pycp — MCP-сервер: временные python-скрипты для агентов

MCP-сервер (stdio), который даёт агентам OpenCode безопасный по смыслу доступ к python:
**вызывать существующие файлы нельзя** — скрипт можно запустить только после того,
как он создан через сам pycp (`write_python_script`). Управляемый скрипт автоматически
**удаляется через 5 минут после последнего использования**; любой вызов сбрасывает таймер.

> Скрипты выполняются **с полными правами пользователя, без песочницы**. Запущенный код может делать что угодно в рамках прав учётной записи.

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

## Как это работает

- `write_python_script(name, code)` — единственный способ добавить скрипт в pycp.
  Файл пишется в managed-папку `pycp/scripts/<name>.py`.
- `run_python(name, ...)` — запускает **только** managed-скрипт по имени.
  Попытка запустить произвольный файл (которого нет в managed-папке/state.json) отклоняется.
- После последнего использования скрипт хранится `PYCP_SCRIPT_TTL` секунд (по умолчанию **300**),
  затем фоновый свипер (каждые `PYCP_SWEEP_INTERVAL` секунд, по умолчанию 15) удаляет файл.
  Создание, запуск, `list_python_scripts` — любой вызов сбрасывает таймер для соответствующего скрипта.
- Расписание удалений хранится в `state.json` и переживает перезапуск сервера:
  при старте просроченные скрипты удаляются сразу, остальным таймер перезаводится.
  Файлы в managed-папке без записи в `state.json` тоже удаляются (они могли появиться только через pycp).

## Инструменты

| Инструмент | Описание |
|---|---|
| `write_python_script(name, code)` | Создать/перезаписать managed-скрипт |
| `run_python(name, args=[], flags={}, stdin="", timeout_seconds=60, cwd="", python="", env={}, max_output_chars=50000)` | Запустить managed-скрипт, вернуть exit code, stdout, stderr |
| `run_python_code(code, args=[], flags={}, stdin="", ...)` | Выполнить inline-код через `python -c` (файл не создаётся) |
| `list_python_scripts()` | Список managed-скриптов и сколько секунд до автоудаления |
| `delete_python_script(name)` | Удалить скрипт немедленно |
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

По умолчанию выставляются `PYTHONUTF8=1` и `PYTHONIOENCODING=utf-8` (если не заданы);
перекрываются через `env`.

## Примеры вызовов агентом

```text
write_python_script(name="resize", code="""
import sys, json
print(json.dumps({"args": sys.argv[1:]}, ensure_ascii=False))
""")

run_python(name="resize",
           flags={"quality": "high", "denoise": true, "samples": 256},
           args=["scene.png"],
           stdin="",
           timeout_seconds=600)

list_python_scripts()   # сколько осталось до автоудаления
delete_python_script(name="resize")   # удалить сразу, не дожидаясь таймера
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
| `PYCP_PYTHON` | Интерпретатор для запуска | `python` из PATH |
| `PYCP_TIMEOUT` | Таймаут по умолчанию, сек | `60` |
| `PYCP_MAX_TIMEOUT` | Максимально допустимый таймаут, сек | `600` |
| `PYCP_MAX_OUTPUT` | Лимит символов stdout/stderr | `50000` |
| `PYCP_SCRIPT_TTL` | Через сколько секунд после последнего использования удалять скрипт | `300` (5 минут) |
| `PYCP_SWEEP_INTERVAL` | Как часто фоновый свипер проверяет TTL, сек | `15` |
| `PYCP_SCRIPTS_DIR` | Managed-папка со скриптами | `pycp/scripts` |
| `PYCP_STATE` | Путь к файлу расписания удалений | `pycp/state.json` |

## Замечания

- Имя скрипта: латиница/цифры/`_`/`-`/`.`, без разделителей путей, до 64 символов. `.py` добавляется автоматически.
- `cwd` по умолчанию — cwd сервера (обычно корень проекта); задаётся параметром per-call.
- При таймауте процесс убивается; возвращаются уже накопленный вывод и отметка о таймауте.
- Удаление best-effort: если файл занят и не удаляется, запись в расписании выкидывается,
  а файл подберёт следующий проход свипера (как «осиротевший»).
- Скрипты получают те же права, что и пользователь: никакой изоляции нет.
- `state.json` и `scripts/` в `.gitignore`.
