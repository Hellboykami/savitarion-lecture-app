"""Звук: аргументы ffmpeg и whisper-cli, склейка частей, длительность,
дата записи через ffprobe и разбор строк прогресса whisper-cli."""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import wave
from pathlib import Path

from .config import Tools

CHUNK_FRAMES = 16000 * 30  # по 30 секунд звука за раз: запись не держим в памяти

SOURCE_META = "дата записи в файле"
SOURCE_NAME = "дата в имени файла"
SOURCE_TODAY = "сегодня"
TAG_PRIORITY = ("com.apple.quicktime.creationdate", "creation_time", "date")


def ffmpeg_convert_args(src: Path, dst: Path) -> list[str]:
    """Любой файл -> WAV 16 кГц, моно, 16 бит. Неинтерактивно (-nostdin);
    dst — новое уникальное имя во временной папке, поэтому вопроса о перезаписи нет."""
    return ["-nostdin", "-i", str(src), "-ar", "16000", "-ac", "1",
            "-c:a", "pcm_s16le", str(dst)]


def whisper_args(model: Path, wav: Path, out_base: Path) -> list[str]:
    """Проверенная цепочка из CLAUDE.md. Не менять без прямой просьбы пользователя:
    -mc 0 обязателен (без него петля), -nf не добавлять никогда, язык только ru.
    -pp — строки прогресса для полосы на странице."""
    return ["-m", str(model), "-f", str(wav), "-l", "ru", "-mc", "0",
            "-otxt", "-osrt", "-of", str(out_base), "-pp"]


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def concat_wavs(parts: list[Path], out: Path) -> float:
    """Склеивает WAV одного формата в показанном порядке. Возвращает длительность."""
    params = None
    frames = 0
    with wave.open(str(out), "wb") as w:
        for part in parts:
            with wave.open(str(part), "rb") as r:
                p = (r.getnchannels(), r.getsampwidth(), r.getframerate())
                if params is None:
                    params = p
                    w.setnchannels(p[0])
                    w.setsampwidth(p[1])
                    w.setframerate(p[2])
                elif p != params:
                    raise ValueError("части записи получились в разном формате")
                while True:
                    data = r.readframes(CHUNK_FRAMES)
                    if not data:
                        break
                    w.writeframes(data)
                    frames += len(data) // (p[0] * p[1])
    return frames / float(params[2]) if params else 0.0


# --- дата записи -----------------------------------------------------------

_ISO = re.compile(
    r"^\s*(\d{4})-(\d{2})-(\d{2})"
    r"(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?)?\s*$"
)
_NAME_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def parse_stamp(value: str, tz: dt.tzinfo | None = None) -> dt.date | None:
    """Дата из метки вида 2026-09-07T15:32:56.000000Z — в местном времени
    (или в поясе tz, если он задан: так делают тесты)."""
    m = _ISO.match(str(value))
    if not m:
        return None
    y, mo, d, hh, mm, ss, zone = m.groups()
    try:
        if hh is None:
            return dt.date(int(y), int(mo), int(d))
        moment = dt.datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0))
    except ValueError:
        return None
    if zone is None:
        return moment.date()  # время без пояса считаем местным
    if zone == "Z":
        offset = dt.timedelta(0)
    else:
        digits = zone[1:].replace(":", "")
        offset = dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        if zone[0] == "-":
            offset = -offset
    aware = moment.replace(tzinfo=dt.timezone(offset))
    local = aware.astimezone(tz) if tz else aware.astimezone()
    return local.date()


def probe(tools: Tools, path: Path, timeout: float = 30.0) -> dict | None:
    """Сведения ffprobe о файле или None, если ffprobe нет или файл не читается."""
    if not tools.ffprobe:
        return None
    cmd = tools.ffprobe + ["-v", "error", "-print_format", "json",
                           "-show_format", "-show_streams", str(path)]
    try:
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    try:
        info = json.loads(res.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None
    return info if isinstance(info, dict) else None


def probe_tags(info: dict) -> dict:
    tags = {}
    for stream in info.get("streams") or []:
        for key, value in (stream.get("tags") or {}).items():
            tags.setdefault(str(key).lower(), value)
    for key, value in ((info.get("format") or {}).get("tags") or {}).items():
        tags[str(key).lower()] = value
    return tags


def probe_duration(info: dict | None) -> float | None:
    try:
        return float(info["format"]["duration"])  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return None


def date_from_tags(tags: dict, today: dt.date, tz: dt.tzinfo | None = None) -> dt.date | None:
    for key in TAG_PRIORITY:
        if key in tags:
            found = parse_stamp(tags[key], tz)
            if found and found.year >= 2000 and found <= today + dt.timedelta(days=1):
                return found
    return None


def date_from_name(name: str) -> dt.date | None:
    for m in _NAME_DATE.finditer(name):
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
    return None


def choose_date(tools: Tools, recordings: list[tuple[Path, str]], today: dt.date,
                tz: dt.tzinfo | None = None) -> tuple[dt.date, str]:
    """Дата лекции и откуда она взята: дата записи внутри файла -> ГГГГ-ММ-ДД
    в имени аудиофайла -> сегодня. recordings — пары (путь, исходное имя)."""
    for path, _name in recordings:
        info = probe(tools, path)
        if info:
            found = date_from_tags(probe_tags(info), today, tz)
            if found:
                return found, SOURCE_META
    for _path, name in recordings:
        found = date_from_name(name)
        if found:
            return found, SOURCE_NAME
    return today, SOURCE_TODAY


# --- прогресс whisper-cli ----------------------------------------------------

_PROGRESS = re.compile(r"progress\s*=\s*(\d+)\s*%")
_SEGMENT = re.compile(r"^\s*\[(\d+):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*"
                      r"(\d+):(\d{2}):(\d{2})[.,](\d{3})\]")


def progress_from_line(line: str, duration: float) -> float | None:
    """Доля готовности 0..1 по строке whisper-cli: «progress = 35%» (ключ -pp)
    или «[00:12:01.000 --> 00:12:05.000] текст» (конец сегмента / длительность)."""
    m = _PROGRESS.search(line)
    if m:
        return min(1.0, int(m.group(1)) / 100.0)
    m = _SEGMENT.match(line)
    if m and duration > 0:
        end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7))
        return min(1.0, (end + int(m.group(8)) / 1000.0) / duration)
    return None
