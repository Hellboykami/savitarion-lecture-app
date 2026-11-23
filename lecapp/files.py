"""Имена и перенос файлов.

Очистка имён от путей, неперезапись « (2)», порядок с учётом чисел,
виды файлов и уборка только внутри своей временной папки.
"""
from __future__ import annotations

import os
import re
import shutil
import unicodedata
from pathlib import Path

KIND_RECORD = "record"
KIND_NOTES = "notes"
KIND_SLIDES = "slides"
KIND_OTHER = "other"
KINDS = (KIND_RECORD, KIND_NOTES, KIND_SLIDES, KIND_OTHER)
KIND_LABELS = {
    KIND_RECORD: "запись",
    KIND_NOTES: "конспекты",
    KIND_SLIDES: "слайды",
    KIND_OTHER: "прочее",
}

RECORD_EXT = frozenset(
    ".m4a .mp3 .ogg .oga .opus .wav .aac .flac .wma .amr .aif .aiff .caf .3gp "
    ".mp4 .m4v .mov .mkv .avi .webm .mpg .mpeg .wmv".split()
)
NOTES_EXT = frozenset(".md .txt .docx .pdf".split())
SLIDES_EXT = frozenset(".jpg .jpeg .png .heic .heif .webp .gif .bmp .tif .tiff".split())

JUNK_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
MAX_NAME_BYTES = 200
FALLBACK_NAME = "файл"
_NUM = re.compile(r"(\d+)")


def split_ext(name: str) -> tuple[str, str]:
    return os.path.splitext(name)


def ascii_ext(name: str) -> str:
    """Расширение для латинского имени во временной папке ('' если оно не латинское)."""
    ext = split_ext(name)[1].lower()
    if ext.isascii() and ext[1:].isalnum() and len(ext) <= 6:
        return ext
    return ""


def classify(name: str) -> str:
    ext = split_ext(name)[1].lower()
    if ext in RECORD_EXT:
        return KIND_RECORD
    if ext in NOTES_EXT:
        return KIND_NOTES
    if ext in SLIDES_EXT:
        return KIND_SLIDES
    return KIND_OTHER


def is_junk(name: str) -> bool:
    """Служебные файлы мака и Windows, которые попадают при перетаскивании папки."""
    return name.lower() in JUNK_NAMES or name.startswith("._")


def safe_name(name: str) -> str:
    """Оставляет от имени только последнюю часть: без папок, ../, / и \\."""
    name = unicodedata.normalize("NFC", str(name))
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f")
    name = name.replace(":", "-")
    name = name.strip().lstrip(".").strip()
    if not name:
        name = FALLBACK_NAME
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        stem, ext = split_ext(name)
        if len(ext.encode("utf-8")) > 20:
            stem, ext = name, ""
        budget = MAX_NAME_BYTES - len(ext.encode("utf-8"))
        stem = stem.encode("utf-8")[:budget].decode("utf-8", "ignore").rstrip()
        name = (stem or FALLBACK_NAME) + ext
    return name


def natural_key(name: str) -> tuple:
    """Ключ сортировки, при котором «photo (2)» раньше «photo (10)»."""
    stem, ext = split_ext(name)
    parts = _NUM.split(stem.casefold())
    key = tuple(int(p) if i % 2 else p for i, p in enumerate(parts))
    return (key, ext.casefold(), name)


def candidate_names(name: str):
    yield name
    stem, ext = split_ext(name)
    for n in range(2, 10000):
        yield f"{stem} ({n}){ext}"


def _copy_exclusive(src: Path, dst: Path) -> None:
    with open(src, "rb") as fin, open(dst, "xb") as fout:
        try:
            shutil.copyfileobj(fin, fout, 1024 * 1024)
        except BaseException:
            fout.close()
            os.unlink(dst)  # недописанный файл создан нами же — убираем
            raise
    shutil.copystat(src, dst)


def move_unique(src: Path, dst_dir: Path, name: str) -> Path:
    """Переносит src в dst_dir под именем name. Ничего не перезаписывает:
    если имя занято — « (2)», « (3)»… src должен лежать в нашей временной папке."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for cand in candidate_names(name):
        dst = dst_dir / cand
        if os.path.lexists(dst):
            continue
        try:
            os.link(src, dst)
        except FileExistsError:
            continue
        except OSError:
            try:
                _copy_exclusive(src, dst)
            except FileExistsError:
                continue
        os.unlink(src)
        return dst
    raise OSError(f"слишком много файлов с именем {name}")


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def is_inside(path: Path, parent: Path) -> bool:
    p, root = _norm(path), _norm(parent)
    try:
        return os.path.commonpath([p, root]) == root
    except ValueError:
        return False


def remove_inside(path: Path, root: Path) -> None:
    """Удаляет папку, только если она внутри root (своей временной папки)."""
    if not is_inside(path, root) or _norm(path) == _norm(root):
        raise ValueError(f"отказ удалять вне временной папки: {path}")
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.lexists(path):
        os.unlink(path)


def clear_dir(path: Path) -> int:
    """Убирает всё содержимое своей временной папки. Возвращает число элементов."""
    if not path.is_dir():
        return 0
    count = 0
    for child in list(path.iterdir()):
        remove_inside(child, path)
        count += 1
    return count
