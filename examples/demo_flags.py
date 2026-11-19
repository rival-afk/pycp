"""Демонстрационный скрипт для pycp: парсит флаги и позиционные аргументы, читает stdin.

Примеры запуска через pycp:
  run_python(script="examples/demo_flags.py", flags={"name": "мир", "verbose": True, "count": 3}, args=["pos1"], stdin="данные")
"""

import argparse
import json
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="demo: флаги + stdin")
    parser.add_argument("--name", default="agent")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("positional", nargs="*")
    ns = parser.parse_args()

    stdin_data = sys.stdin.read() if not sys.stdin.isatty() else ""

    print(json.dumps(
        {
            "name": ns.name,
            "count": ns.count,
            "verbose": ns.verbose,
            "positional": ns.positional,
            "stdin": stdin_data,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
