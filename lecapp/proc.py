"""Запуск внешних программ с возможностью отмены и техническим журналом.

На маке программа запускается в своей группе процессов (новая сессия),
и отмена останавливает всю группу — caffeinate и whisper-cli вместе.
На Windows (только для проверок) останавливается сам процесс.
Программы запускаются списком аргументов, без оболочки.
"""
from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

LineHandler = Optional[Callable[[str, str], None]]
_SPLIT = re.compile(rb"[\r\n]")


class Cancelled(Exception):
    """Пользователь отменил задачу."""


class RunLog:
    """Технический журнал одного прогона: logs/<время>_<задача>.log."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._fh = open(path, "a", encoding="utf-8", newline="\n")

    def write(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        with self._lock:
            if self._fh.closed:
                return
            for line in str(text).splitlines() or [""]:
                self._fh.write(f"{stamp} {line}\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()


def quote_cmd(cmd: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in cmd)


def stop_process(proc: subprocess.Popen, grace: float = 5.0) -> None:
    """Останавливает процесс; на маке — всю его группу (caffeinate + whisper-cli)."""
    if proc.poll() is not None:
        return
    if os.name == "posix" and hasattr(os, "killpg"):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:
            proc.terminate()
        try:
            proc.wait(grace)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    else:
        proc.terminate()
        try:
            proc.wait(grace)
        except subprocess.TimeoutExpired:
            proc.kill()


def _pump(stream, label: str, handle: Callable[[str, str], None]) -> None:
    """Читает вывод кусками и режет на строки по \\n и \\r (ffmpeg пишет через \\r)."""
    buf = b""
    while True:
        chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
        if not chunk:
            break
        buf += chunk
        parts = _SPLIT.split(buf)
        buf = parts.pop()
        for part in parts:
            if part:
                handle(label, part.decode("utf-8", "replace"))
        if len(buf) > 1_000_000:
            handle(label, buf.decode("utf-8", "replace"))
            buf = b""
    if buf:
        handle(label, buf.decode("utf-8", "replace"))


class ProcessRunner:
    """Запускает программы одну за другой; cancel() останавливает текущую."""

    def __init__(self, log: RunLog, keep_awake: list[str] | None = None) -> None:
        self.log = log
        self.keep_awake = keep_awake  # ["caffeinate", "-i"] на маке; None — такой программы нет
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._cancelled = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def current_pid(self) -> int | None:
        with self._lock:
            return self._proc.pid if self._proc is not None else None

    def check(self) -> None:
        if self._cancelled.is_set():
            raise Cancelled()

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            proc = self._proc
        if proc is not None:
            stop_process(proc)

    def run(self, cmd: list[str], cwd: Path | None = None, on_line: LineHandler = None,
            awake: bool = False, quiet_stdout: bool = False) -> int:
        """Запускает cmd и ждёт конца. awake — не давать маку уснуть (caffeinate -i).
        quiet_stdout — в журнал попадают только последние строки stdout
        (у whisper-cli там текст лекции)."""
        full = [str(part) for part in cmd]
        if awake and self.keep_awake:
            full = list(self.keep_awake) + full
        self.log.write("$ " + quote_cmd(full))
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": str(cwd) if cwd else None,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        tail: deque = deque(maxlen=20)

        def handle(label: str, line: str) -> None:
            if label == "stdout" and quiet_stdout:
                tail.append(line)
            else:
                self.log.write(f"[{label}] {line}")
            if on_line is not None:
                on_line(label, line)

        with self._lock:
            if self._cancelled.is_set():
                raise Cancelled()
            proc = subprocess.Popen(full, **kwargs)
            self._proc = proc
        readers = [
            threading.Thread(target=_pump, args=(proc.stdout, "stdout", handle), daemon=True),
            threading.Thread(target=_pump, args=(proc.stderr, "stderr", handle), daemon=True),
        ]
        for t in readers:
            t.start()
        try:
            code = proc.wait()
        finally:
            for t in readers:
                t.join(timeout=10)
            with self._lock:
                self._proc = None
        if tail:
            self.log.write("последние строки вывода:")
            for line in tail:
                self.log.write(f"[stdout] {line}")
        self.log.write(f"код выхода: {code}")
        if self._cancelled.is_set():
            raise Cancelled()
        return code
