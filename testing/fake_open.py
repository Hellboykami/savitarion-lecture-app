"""Имитация программы open (открыть страницу или папку): дописывает полученные
аргументы одной строкой JSON в файл из переменной окружения FAKE_OPEN_LOG."""
from __future__ import annotations

import json
import os
import sys

if __name__ == "__main__":
    log_path = os.environ.get("FAKE_OPEN_LOG")
    if log_path:
        with open(log_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\n")
