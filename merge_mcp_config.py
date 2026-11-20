#!/usr/bin/env python3
"""Вставляет запись pycp в конфиг OpenCode (opencode.json / opencode.jsonc).

Используется install.ps1 / install.sh, когда `opencode` CLI отсутствует в PATH:
вместо «напечатать JSON и тихо выйти» инсталлер может дописать сервер в уже
существующий конфиг, сохранив комментарии, форматирование и остальные ключи.

Usage:
    python merge_mcp_config.py <config> <python_exe> <server.py> [dest_dir]

Exit code: 0 — успешно, 1 — не удалось (сообщение об ошибке в stderr).
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Optional


def mask_jsonc(text: str) -> str:
    """Заменить комментарии и завершающие запятые пробелами, сохранив длину текста.

    Благодаря сохранению смещений можно править оригинальный текст (реальный
    конфиг с комментариями), при этом валидировать результат как обычный JSON.
    """
    out = list(text)
    i, n = 0, len(text)
    in_string = False
    escaped = False
    while i < n:
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            out[i] = out[i + 1] = " "
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                if text[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:
                out[i] = out[i + 1] = " "
                i += 2
            continue
        i += 1

    masked = "".join(out)
    # trailing commas: , перед } или ] (пробелы между ними допустимы)
    while True:
        match = re.search(r",(\s*[}\]])", masked)
        if not match:
            break
        idx = match.start()
        masked = masked[:idx] + " " + masked[idx + 1 :]
    return masked


def matching_brace(masked: str, open_idx: int) -> Optional[int]:
    """Индекс закрывающей скобки для '{' в open_idx (строки игнорируются)."""
    depth = 0
    in_string = False
    escaped = False
    for i in range(open_idx, len(masked)):
        ch = masked[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def find_key_object(masked: str, key: str, start: int, end: int):
    """Найти (open, close) объекта-значения ключа key в диапазоне [start, end)."""
    match = re.search(r'"' + re.escape(key) + r'"\s*:\s*\{', masked[start:end])
    if not match:
        return None
    abs_start = start + match.start()
    open_idx = masked.index("{", abs_start, end)
    close_idx = matching_brace(masked, open_idx)
    if close_idx is None or close_idx > end:
        return None
    return open_idx, close_idx


def last_nonspace(text: str, start: int, end: int) -> int:
    i = end - 1
    while i >= start and text[i].isspace():
        i -= 1
    return i


def indent_inside(text: str, open_idx: int) -> str:
    """Отступ для полей внутри объекта, открытого в open_idx."""
    line_start = text.rfind("\n", 0, open_idx) + 1
    prefix = text[line_start:open_idx]
    match = re.match(r"[ \t]*", prefix)
    base = match.group(0) if match else ""
    return base + "  "


def format_value(value: dict, indent: str) -> str:
    raw = json.dumps(value, ensure_ascii=False, indent=2)
    lines = raw.splitlines()
    return lines[0] + "\n" + "\n".join(indent + line for line in lines[1:])


def add_or_replace_member(text: str, masked: str, open_idx: int, close_idx: int, key: str, value: dict) -> str:
    """Добавить/заменить ключ key в объекте, не трогая остальной текст."""
    indent = indent_inside(text, open_idx)
    value_txt = format_value(value, indent)
    existing = find_key_object(masked, key, open_idx, close_idx + 1)
    if existing is not None:
        val_open, val_close = existing
        return text[:val_open] + value_txt + text[val_close + 1 :]
    # Ищем последний значимый символ по «маске»: комментарии в ней заменены
    # пробелами, поэтому завершающая запятая находится корректно даже в JSONC.
    idx = last_nonspace(masked, open_idx + 1, close_idx)
    if idx < open_idx + 1:
        insert_at = open_idx + 1
        addition = "\n" + indent + '"' + key + '": ' + value_txt
    else:
        insert_at = idx + 1
        prefix = "" if masked[idx] == "," else ","
        addition = prefix + "\n" + indent + '"' + key + '": ' + value_txt
    return text[:insert_at] + addition + text[insert_at:]


def merge(config_path: str, python_exe: str, server_py: str, dest_dir: str) -> None:
    with open(config_path, "r", encoding="utf-8") as fh:
        text = fh.read()

    masked = mask_jsonc(text)
    data = json.loads(masked)
    if not isinstance(data, dict):
        raise ValueError("корень конфига должен быть JSON-объектом")

    root_open = masked.find("{")
    root_close = matching_brace(masked, root_open) if root_open >= 0 else None
    if root_close is None:
        raise ValueError("не найден корневой объект JSON")

    entry = {
        "type": "local",
        "command": [python_exe.replace("\\", "/"), server_py.replace("\\", "/")],
        "cwd": dest_dir.replace("\\", "/"),
    }

    mcp = find_key_object(masked, "mcp", root_open, root_close + 1)
    if mcp is None:
        new_text = add_or_replace_member(text, masked, root_open, root_close, "mcp", {"servers": {"pycp": entry}})
    else:
        mcp_open, mcp_close = mcp
        servers = find_key_object(masked, "servers", mcp_open, mcp_close + 1)
        if servers is None:
            new_text = add_or_replace_member(text, masked, mcp_open, mcp_close, "servers", {"pycp": entry})
        else:
            s_open, s_close = servers
            new_text = add_or_replace_member(text, masked, s_open, s_close, "pycp", entry)

    # результат должен оставаться валидным JSON(C)
    json.loads(mask_jsonc(new_text))

    tmp_path = config_path + ".pycp.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    os.replace(tmp_path, config_path)


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: merge_mcp_config.py <config> <python_exe> <server.py> [dest_dir]", file=sys.stderr)
        return 1
    config_path, python_exe, server_py = sys.argv[1:4]
    dest_dir = sys.argv[4] if len(sys.argv) > 4 else os.path.dirname(server_py)
    try:
        merge(config_path, python_exe, server_py, dest_dir)
    except Exception as exc:
        print(f"pycp: не удалось обновить конфиг {config_path}: {exc}", file=sys.stderr)
        return 1
    print(f"pycp: зарегистрирован в конфиге {config_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
