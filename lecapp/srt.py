"""Чтение .srt и проверка на петлю.

Петля — N сегментов подряд с одинаковым текстом без учёта регистра,
пробелов и знаков препинания. N приходит из настроек (по умолчанию
config.DEFAULT_LOOP_THRESHOLD). Сегменты без букв и цифр петлёй не считаются.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TIME = r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
_LINE = re.compile(r"^\s*" + _TIME + r"\s*-->\s*" + _TIME)


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class LoopHit:
    start: float
    end: float
    count: int
    text: str


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    ms = (ms + "00")[:3]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(text: str) -> list[Segment]:
    lines = text.lstrip(chr(0xFEFF)).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segments = []
    i = 0
    while i < len(lines):
        m = _LINE.match(lines[i])
        if not m:
            i += 1
            continue
        start = _seconds(*m.group(1, 2, 3, 4))
        end = _seconds(*m.group(5, 6, 7, 8))
        i += 1
        body = []
        while i < len(lines) and lines[i].strip():
            if _LINE.match(lines[i]):
                if body and body[-1].isdigit():
                    body.pop()  # номер следующего блока без пустой строки перед ним
                break
            body.append(lines[i].strip())
            i += 1
        segments.append(Segment(start, end, " ".join(body).strip()))
    return segments


def read_srt(path: Path) -> list[Segment]:
    return parse_srt(Path(path).read_text(encoding="utf-8-sig", errors="replace"))


def ts_srt(sec: float) -> str:
    total_ms = int(round(sec * 1000))
    h, rest = divmod(total_ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ts_human(sec: float) -> str:
    total = int(sec)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_srt(segments: list[Segment]) -> str:
    blocks = []
    for n, seg in enumerate(segments, 1):
        blocks.append(f"{n}\n{ts_srt(seg.start)} --> {ts_srt(seg.end)}\n{seg.text}\n")
    return "\n".join(blocks)


def normalize(text: str) -> str:
    """Без регистра, пробелов и знаков препинания: остаются буквы и цифры."""
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def find_loops(segments: list[Segment], threshold: int) -> list[LoopHit]:
    """Все серии из threshold и более одинаковых сегментов подряд."""
    threshold = max(2, int(threshold))
    hits = []
    run_start = 0
    prev = None
    for i in range(len(segments) + 1):
        cur = normalize(segments[i].text) if i < len(segments) else None
        if i < len(segments) and cur and cur == prev:
            continue
        length = i - run_start
        if prev and length >= threshold:
            first, last = segments[run_start], segments[i - 1]
            hits.append(LoopHit(first.start, last.end, length, first.text.strip()))
        run_start = i
        prev = cur
    return hits


def check_file(path: Path, threshold: int) -> list[LoopHit]:
    return find_loops(read_srt(path), threshold)
