"""Имитация osascript: дописывает полученные аргументы одной строкой JSON
в файл из переменной окружения FAKE_OSASCRIPT_LOG. Так selftest видит,
что текст уведомления пришёл отдельными аргументами, а не вставкой в скрипт."""
from __future__ import annotations

import json
import os
import sys

if __name__ == "__main__":
    log_path = os.environ.get("FAKE_OSASCRIPT_LOG")
    if log_path:
        with open(log_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\n")
