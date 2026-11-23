"""Общее для имитаций: WAV и свой блок метаданных «fktg».

Настоящий ffmpeg пишет дату записи в метаданные .m4a; имитация вместо этого
дописывает в конец WAV блок fktg с JSON. Модуль wave этот блок не замечает,
а fake_ffprobe читает его и отдаёт как теги.
"""
from __future__ import annotations

import json
import struct
import wave

CHUNK = 16000 * 10


def read_tags(path: str) -> dict:
    tags: dict = {}
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
            if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                return tags
            while True:
                hdr = fh.read(8)
                if len(hdr) < 8:
                    break
                cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
                if cid == b"fktg":
                    tags.update(json.loads(fh.read(size).decode("utf-8")))
                    break
                fh.seek(size + (size & 1), 1)
    except (OSError, ValueError):
        pass
    return tags


def append_tags(path: str, tags: dict) -> None:
    if not tags:
        return
    payload = json.dumps(tags, ensure_ascii=False).encode("utf-8")
    with open(path, "r+b") as fh:
        fh.seek(0, 2)
        fh.write(b"fktg" + struct.pack("<I", len(payload)) + payload)
        if len(payload) & 1:
            fh.write(b"\0")
        end = fh.tell()
        fh.seek(4)
        fh.write(struct.pack("<I", end - 8))


def write_silence(path: str, seconds: float) -> None:
    """WAV 16 кГц моно с тишиной — для демо, когда на вход дали настоящую запись."""
    block = b"\0\0" * 16000
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        for _ in range(int(seconds)):
            w.writeframes(block)


def copy_frames(src: str, dst: str, start: float = 0.0, length: float | None = None) -> float:
    """Копирует звук WAV -> WAV (с обрезкой). Возвращает длительность результата."""
    with wave.open(src, "rb") as r:
        rate = r.getframerate()
        first = min(r.getnframes(), int(round(start * rate)))
        last = r.getnframes()
        if length is not None:
            last = min(last, first + int(round(length * rate)))
        r.setpos(first)
        with wave.open(dst, "wb") as w:
            w.setnchannels(r.getnchannels())
            w.setsampwidth(r.getsampwidth())
            w.setframerate(rate)
            left = last - first
            while left > 0:
                data = r.readframes(min(CHUNK, left))
                if not data:
                    break
                w.writeframes(data)
                left -= len(data) // (r.getnchannels() * r.getsampwidth())
    return (last - first) / float(rate)
