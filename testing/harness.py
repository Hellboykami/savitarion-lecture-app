"""Общая часть selftest: тестовый корень, подменённая домашняя папка,
имитации программ, регистрация проверок и мелкие помощники.

Всё, что пишут проверки, лежит в test-root/ внутри папки проекта.
"""
from __future__ import annotations

import array
import contextlib
import os
import shutil
import sys
import time
import traceback
import wave
from pathlib import Path

from lecapp import config, files, jobs, layout, pipeline, settings

PROJECT = config.PROJECT_DIR
TESTING = PROJECT / "testing"
TEST_ROOT = PROJECT / "test-root"
RATE = 16000

CHECKS: list = []
KEEP_IN_ROOT = {"demo"}  # демо-режим живёт в test-root/demo — selftest его не стирает


class Skip(Exception):
    """Проверка невозможна на этом компьютере (например, только на маке)."""


def check(title: str):
    def register(fn):
        CHECKS.append((title, fn))
        return fn
    return register


def expect(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def fake_cmd(name: str) -> list:
    """Имитация запускается через тот же Python, а не через shebang."""
    return [sys.executable, str(TESTING / f"fake_{name}.py")]


@contextlib.contextmanager
def fake_mode(mode: str, seconds: float | None = None):
    old = {k: os.environ.get(k) for k in ("FAKE_WHISPER_MODE", "FAKE_WHISPER_SECONDS")}
    os.environ["FAKE_WHISPER_MODE"] = mode
    if seconds is not None:
        os.environ["FAKE_WHISPER_SECONDS"] = str(seconds)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def write_wav(path: Path, seconds: float, level: int = 0) -> Path:
    """WAV 16 кГц моно с постоянным уровнем сигнала: по знаку уровня имитация
    whisper понимает, какая часть записи звучит."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * RATE)
    block = array.array("h", [level]) * RATE
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        left = frames
        while left > 0:
            part = block if left >= RATE else block[:left]
            w.writeframes(part.tobytes())
            left -= len(part)
    return path


class Env:
    def __init__(self, use_real_ffmpeg: bool | None = None) -> None:
        self.root = TEST_ROOT
        self.home = self.root / "home"
        self.paths = config.Paths(project=self.root, tmp=self.root / "tmp",
                                  logs=self.root / "logs",
                                  settings_file=self.root / "settings.json", home=self.home)
        self.inputs = self.root / "inputs"
        self.model = self.home / "whisper-models" / "ggml-large-v3-turbo.bin"
        self.notify_log = self.root / "osascript-calls.jsonl"
        self.open_log = self.root / "open-calls.jsonl"
        self.heartbeat = self.root / "whisper-heartbeat.txt"
        real_ffmpeg = config.find_program("ffmpeg")
        real_ffprobe = config.find_program("ffprobe")
        if use_real_ffmpeg is None:
            use_real_ffmpeg = bool(real_ffmpeg and real_ffprobe)
        self.real_ffmpeg = bool(use_real_ffmpeg)
        if self.real_ffmpeg:
            self.ffmpeg, self.ffprobe = [real_ffmpeg], [real_ffprobe]
        else:
            self.ffmpeg, self.ffprobe = fake_cmd("ffmpeg"), fake_cmd("ffprobe")
        caffeinate = config.find_program("caffeinate")
        self.caffeinate = [caffeinate] if caffeinate else None
        self.queues: list = []
        self._cards = 0
        # Были ли настоящие рабочие папки проекта до selftest — он их трогать не должен.
        self.preexisting = {name: (PROJECT / name).exists()
                            for name in ("tmp", "logs", "settings.json")}

    def prepare(self) -> None:
        if self.root.exists():
            for child in list(self.root.iterdir()):
                if child.name not in KEEP_IN_ROOT:
                    files.remove_inside(child, self.root)
        for folder in (self.inputs, self.paths.tmp, self.model.parent):
            folder.mkdir(parents=True, exist_ok=True)
        self.model.write_bytes(b"fake model for selftest\n")
        os.environ["FAKE_OSASCRIPT_LOG"] = str(self.notify_log)
        os.environ["FAKE_WHISPER_HEARTBEAT"] = str(self.heartbeat)
        os.environ["FAKE_OPEN_LOG"] = str(self.open_log)
        for key in ("FAKE_WHISPER_MODE_FILE", "FAKE_FFMPEG_ANY"):
            os.environ.pop(key, None)  # это настройки демо-режима, не проверок

    def close(self) -> None:
        for queue in self.queues:
            queue.shutdown(timeout=10)

    def tools(self, **changes: object) -> config.Tools:
        tools = config.Tools(ffmpeg=list(self.ffmpeg), ffprobe=list(self.ffprobe),
                             whisper=fake_cmd("whisper"), caffeinate=self.caffeinate,
                             osascript=fake_cmd("osascript"), open=fake_cmd("open"),
                             model=self.model)
        for key, value in changes.items():
            setattr(tools, key, value)
        return tools

    def settings(self) -> settings.Settings:
        return settings.load_settings(self.paths.settings_file)

    def study_root(self) -> Path:
        return self.settings().study_root_path(self.home)

    def queue(self, tools: config.Tools | None = None) -> jobs.JobQueue:
        ctx = pipeline.Context(paths=self.paths, tools=tools or self.tools(),
                               get_settings=self.settings)
        queue = jobs.JobQueue(ctx)
        self.queues.append(queue)
        return queue

    def wav(self, name: str, seconds: float, level: int = 0) -> Path:
        return write_wav(self.inputs / name, seconds, level)

    def stage(self, sources: list) -> list:
        """Кладёт файлы в папку карточки во временной папке, как сделает страница.
        sources — пары (исходный путь, исходное имя) или тройки с видом."""
        self._cards += 1
        card = self.paths.tmp / "cards" / f"card{self._cards}"
        card.mkdir(parents=True, exist_ok=True)
        items = []
        for k, source in enumerate(sources, 1):
            src, name = Path(source[0]), source[1]
            clean = files.safe_name(name)
            kind = source[2] if len(source) > 2 else files.classify(clean)
            dst = card / f"f{k}{files.split_ext(clean)[1].lower()}"
            shutil.copyfile(src, dst)
            items.append(layout.Item(dst, clean, kind))
        return items

    def request(self, subject: str, date: str, items: list, join: bool = False):
        staging = items[0].path.parent if items else None
        return pipeline.LectureRequest(subject=subject, date=date, items=items,
                                       join_parts=join, staging=staging)


def run_one(queue: jobs.JobQueue, request, timeout: float = 60.0):
    job = queue.submit(request)
    return queue.wait(job.id, timeout)


def tree_lines(path: Path, prefix: str = "") -> list:
    lines = []
    entries = sorted(path.iterdir(), key=lambda p: files.natural_key(p.name))
    for entry in entries:
        if entry.is_dir():
            lines.append(f"{prefix}{entry.name}/")
            lines.extend(tree_lines(entry, prefix + "    "))
        else:
            lines.append(f"{prefix}{entry.name}")
    return lines


def run_checks(env: Env) -> tuple:
    """Возвращает (сколько не прошло, сколько пропущено на этом компьютере)."""
    failed = skipped = 0
    for title, fn in CHECKS:
        started = time.time()
        try:
            fn(env)
        except Skip as why:
            skipped += 1
            print(f"  ПРОПУСК {title} — {why}")
        except Exception as err:  # noqa: BLE001 — любая поломка = проверка не прошла
            failed += 1
            print(f"  ОШИБКА  {title}")
            detail = str(err) if isinstance(err, AssertionError) else traceback.format_exc()
            for line in detail.strip().splitlines():
                print(f"          {line}")
        else:
            print(f"  ОК      {title} ({time.time() - started:.1f} с)")
        sys.stdout.flush()
    return failed, skipped
