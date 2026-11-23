"""Где лежат программы, модель и рабочие папки.

Пути к внешним программам задаются только здесь. selftest подставляет
вместо них имитации, передавая свой объект Tools.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

# Программы на маке пользователя (Homebrew и системные).
PROGRAM_PATHS = {
    "ffmpeg": "/opt/homebrew/bin/ffmpeg",
    "ffprobe": "/opt/homebrew/bin/ffprobe",
    "whisper": "/opt/homebrew/bin/whisper-cli",
    "caffeinate": "/usr/bin/caffeinate",
    "osascript": "/usr/bin/osascript",
    "open": "/usr/bin/open",
}
# Под какими именами искать программу, если по пути выше её нет.
PROGRAM_NAMES = {
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffprobe",
    "whisper": "whisper-cli",
    "caffeinate": "caffeinate",
    "osascript": "osascript",
    "open": "open",
}
EXTRA_SEARCH_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
MODEL_PATH = "~/whisper-models/ggml-large-v3-turbo.bin"

DEFAULT_STUDY_ROOT = "~/study"
DEFAULT_SUBJECTS = ("ПЭК", "Авторское право", "Legal Tech", "Семинар наставника")
# N для проверки на петлю: столько одинаковых сегментов подряд — уже петля.
# Единственное место, где задано это число; пользователь меняет его в settings.json.
DEFAULT_LOOP_THRESHOLD = 5

# Грубая оценка времени распознавания на маке пользователя:
# 90 минут записи — около 14 минут, 15 минут — около 4 минут.
ESTIMATE_OVERHEAD_S = 75.0
ESTIMATE_FACTOR = 0.145

# Сколько файлов за раз принимает чат Claude.
CLAUDE_UPLOAD_LIMIT = 20

# Адрес страницы: http://127.0.0.1:PORT/. Порт постоянный — по нему второй
# запуск находит уже работающее приложение. Демо-режим — на соседнем порту.
PORT = 48731
DEMO_PORT = 48732


def is_mac() -> bool:
    return sys.platform == "darwin"


def expand_home(value: str, home: Path) -> Path:
    """Разворачивает ~ от заданной домашней папки (в тестах — подменённой)."""
    if value == "~":
        return Path(home)
    if value.startswith("~/") or value.startswith("~\\"):
        return Path(home) / value[2:]
    return Path(value)


def find_program(key: str) -> str | None:
    configured = PROGRAM_PATHS[key]
    if os.path.isfile(configured) and os.access(configured, os.X_OK):
        return configured
    search = os.pathsep.join([os.environ.get("PATH", "")] + list(EXTRA_SEARCH_DIRS))
    return shutil.which(PROGRAM_NAMES[key], path=search)


@dataclass
class Tools:
    """Команды запуска программ списком аргументов. None — программы нет."""

    ffmpeg: list[str] | None
    ffprobe: list[str] | None
    whisper: list[str] | None
    caffeinate: list[str] | None
    osascript: list[str] | None
    open: list[str] | None
    model: Path


def real_tools(home: Path | None = None) -> Tools:
    home = Path(home) if home else Path.home()

    def cmd(key: str) -> list[str] | None:
        found = find_program(key)
        return [found] if found else None

    return Tools(
        ffmpeg=cmd("ffmpeg"),
        ffprobe=cmd("ffprobe"),
        whisper=cmd("whisper"),
        caffeinate=cmd("caffeinate"),
        osascript=cmd("osascript"),
        open=cmd("open"),
        model=expand_home(MODEL_PATH, home),
    )


@dataclass
class Paths:
    """Рабочие папки приложения. В тестах все они внутри тестового корня."""

    project: Path
    tmp: Path
    logs: Path
    settings_file: Path
    home: Path

    @classmethod
    def for_project(cls, project: Path, home: Path | None = None) -> "Paths":
        project = Path(project)
        return cls(
            project=project,
            tmp=project / "tmp",
            logs=project / "logs",
            settings_file=project / "settings.json",
            home=Path(home) if home else Path.home(),
        )
