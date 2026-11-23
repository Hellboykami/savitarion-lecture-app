#!/usr/bin/env python3
"""Приложение «Расшифровка лекций»: python3 app.py

Обычно его запускает двойной щелчок по Lectures.command.
Для проверки страницы на ноутбуке без whisper-cli: python3 app.py --demo
"""
from __future__ import annotations

import sys

from lecapp import launcher

if __name__ == "__main__":
    sys.exit(launcher.main(sys.argv[1:]))
