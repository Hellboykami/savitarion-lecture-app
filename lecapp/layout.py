"""Раскладка по папке лекции.

<корень учёбы>/<Предмет>/<ГГГГ-ММ-ДД>/
    расшифровка/  <ГГГГ-ММ-ДД> <Предмет> — запись N.srt и .txt
    запись/       исходные аудио и видео как есть
    конспекты/
    слайды/       слайд-01, слайд-02… — по исходным именам с учётом чисел
    прочее/
Если папка лекции уже есть, новое добавляется к ней: нумерация записей
и слайдов продолжается, существующие файлы не трогаются.
Создаются только те подпапки, в которые что-то попадает.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import files, messages

DIR_TRANSCRIPTS = "расшифровка"
DIR_BY_KIND = {
    files.KIND_RECORD: "запись",
    files.KIND_NOTES: "конспекты",
    files.KIND_SLIDES: "слайды",
    files.KIND_OTHER: "прочее",
}
SLIDE_PREFIX = "слайд-"

_RECORD_NUM = re.compile(r"запись (\d+)")
_SLIDE_NUM = re.compile(r"^слайд-(\d+)")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


@dataclass
class Item:
    path: Path  # файл во временной папке
    name: str   # исходное имя, уже очищенное от путей
    kind: str


@dataclass
class Transcript:
    srt: Path
    txt: Path | None


@dataclass
class Placed:
    lecture_dir: Path
    transcripts: list = field(default_factory=list)
    files: list = field(default_factory=list)


def check_date(value: str) -> str:
    m = _DATE.match(str(value).strip())
    if not m:
        raise messages.bad_date(str(value))
    try:
        dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise messages.bad_date(str(value))
    return m.group(0)


def lecture_dir(root: Path, subject: str, date_str: str) -> Path:
    return Path(root) / subject / date_str


def transcript_base(date_str: str, subject: str, number: int) -> str:
    return f"{date_str} {subject} — запись {number}"


def _max_number(folder: Path, pattern: re.Pattern) -> int:
    best = 0
    if folder.is_dir():
        for child in folder.iterdir():
            m = pattern.search(child.name)
            if m:
                best = max(best, int(m.group(1)))
    return best


def next_record_number(lec_dir: Path) -> int:
    return _max_number(lec_dir / DIR_TRANSCRIPTS, _RECORD_NUM) + 1


def next_slide_number(lec_dir: Path) -> int:
    return _max_number(lec_dir / DIR_BY_KIND[files.KIND_SLIDES], _SLIDE_NUM) + 1


def place(lec_dir: Path, date_str: str, subject: str,
          transcripts: list[Transcript], items: list[Item]) -> Placed:
    """Переносит расшифровки и материалы в папку лекции. Вызывается в самом
    конце, только после успеха. Ничего не перезаписывает."""
    result = Placed(lec_dir)
    lec_dir.mkdir(parents=True, exist_ok=True)

    number = next_record_number(lec_dir)
    for tr in transcripts:
        base = transcript_base(date_str, subject, number)
        target = lec_dir / DIR_TRANSCRIPTS
        srt_dst = files.move_unique(tr.srt, target, base + ".srt")
        txt_dst = None
        if tr.txt is not None and tr.txt.exists():
            txt_dst = files.move_unique(tr.txt, target, base + ".txt")
        result.transcripts.append((srt_dst, txt_dst))
        number += 1

    slides = sorted((i for i in items if i.kind == files.KIND_SLIDES),
                    key=lambda i: files.natural_key(files.safe_name(i.name)))
    first = next_slide_number(lec_dir)
    width = max(2, len(str(first + len(slides) - 1)))
    for k, item in enumerate(slides, first):
        ext = files.split_ext(files.safe_name(item.name))[1].lower()
        name = f"{SLIDE_PREFIX}{k:0{width}d}{ext}"
        target = lec_dir / DIR_BY_KIND[files.KIND_SLIDES]
        result.files.append(files.move_unique(item.path, target, name))

    for item in items:
        if item.kind == files.KIND_SLIDES:
            continue
        folder = DIR_BY_KIND.get(item.kind, DIR_BY_KIND[files.KIND_OTHER])
        result.files.append(files.move_unique(item.path, lec_dir / folder,
                                              files.safe_name(item.name)))
    return result


def project_file_count(lec_dir: Path) -> int:
    """Сколько файлов пойдёт в проект Claude: .srt + конспекты + слайды."""
    count = 0
    tr_dir = lec_dir / DIR_TRANSCRIPTS
    if tr_dir.is_dir():
        count += sum(1 for p in tr_dir.iterdir()
                     if p.is_file() and p.suffix.lower() == ".srt")
    for kind in (files.KIND_NOTES, files.KIND_SLIDES):
        folder = lec_dir / DIR_BY_KIND[kind]
        if folder.is_dir():
            count += sum(1 for p in folder.iterdir()
                         if p.is_file() and not files.is_junk(p.name))
    return count
