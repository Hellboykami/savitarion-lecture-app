"""Демо-режим для проверки страницы на ноутбуке: python app.py --demo

Распознавание имитируется (testing/fake_whisper.py), ffmpeg — имитация,
которая принимает любой файл и делает из него короткую «тишину». Всё
пишется в test-root/demo/, настоящие settings.json, tmp/, logs/ и папка
учёбы не трогаются. Режим имитации whisper задаёт файл
test-root/demo/fake-mode.txt: «slow 20» (по умолчанию), «loop», «error», «normal».
"""
from __future__ import annotations

import os

from lecapp import config

from .harness import TEST_ROOT, fake_cmd

DEMO_ROOT = TEST_ROOT / "demo"


def prepare() -> tuple:
    home = DEMO_ROOT / "home"
    paths = config.Paths(project=DEMO_ROOT, tmp=DEMO_ROOT / "tmp", logs=DEMO_ROOT / "logs",
                         settings_file=DEMO_ROOT / "settings.json", home=home)
    model = home / "whisper-models" / "ggml-large-v3-turbo.bin"
    model.parent.mkdir(parents=True, exist_ok=True)
    if not model.exists():
        model.write_bytes(b"fake model for demo\n")
    mode_file = DEMO_ROOT / "fake-mode.txt"
    if not mode_file.exists():
        mode_file.write_text("slow 20\n", encoding="utf-8")
    os.environ["FAKE_WHISPER_MODE_FILE"] = str(mode_file)
    os.environ["FAKE_FFMPEG_ANY"] = "1"
    os.environ["FAKE_OSASCRIPT_LOG"] = str(DEMO_ROOT / "osascript-calls.jsonl")
    caffeinate = config.find_program("caffeinate")
    opener = config.find_program("open")
    tools = config.Tools(ffmpeg=fake_cmd("ffmpeg"), ffprobe=fake_cmd("ffprobe"),
                         whisper=fake_cmd("whisper"),
                         caffeinate=[caffeinate] if caffeinate else None,
                         osascript=fake_cmd("osascript"),
                         open=[opener] if opener else None, model=model)
    return paths, tools
