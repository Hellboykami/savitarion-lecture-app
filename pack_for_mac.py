#!/usr/bin/env python3
"""Архив для мака: python3 pack_for_mac.py

Собирает lecture-app-mac.zip с папкой lecture-app/ внутри и сразу проверяет
готовый архив. Собирать только этим скриптом: архив из проводника Windows
теряет у Lectures.command право на запуск.

В архив не попадают tmp/, logs/, test-root/, settings.json, __pycache__,
сами архивы, а также записи и расшифровки лекций (они охраняются авторским
правом). Текстовые файлы в архиве — UTF-8 без BOM, перевод строки LF.
"""
from __future__ import annotations

import os
import stat
import sys
import time
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
ARCHIVE_NAME = "lecture-app-mac.zip"
TOP = "lecture-app"
EXECUTABLE = {"Lectures.command"}
TEXT_EXT = {".py", ".command", ".md", ".txt", ".html", ".css", ".js", ".json"}
SKIP_DIRS = {"tmp", "logs", "test-root", "__pycache__"}
SKIP_FILES = {"settings.json", ".DS_Store", "Thumbs.db", "desktop.ini"}
SKIP_SUFFIXES = {".zip", ".pyc", ".pyo", ".part"}
# Записи и расшифровки лекций в архив не кладутся никогда.
LECTURE_SUFFIXES = {".m4a", ".mp3", ".ogg", ".oga", ".opus", ".wav", ".aac", ".flac", ".wma",
                    ".amr", ".aif", ".aiff", ".caf", ".3gp", ".mp4", ".m4v", ".mov", ".mkv",
                    ".avi", ".webm", ".mpg", ".mpeg", ".wmv", ".srt"}
REQUIRED = ("Lectures.command", "app.py", "selftest.py", "pack_for_mac.py",
            "lecapp/__init__.py", "lecapp/server.py", "web/index.html",
            "testing/fake_whisper.py", "testing/live.py")
FORBIDDEN_PARTS = SKIP_DIRS
BOM = bytes([0xEF, 0xBB, 0xBF])
UNIX = 3  # «архив сделан в Unix»: только тогда мак применит права из архива


def collect(root: Path) -> tuple:
    """(файлы для архива, пропущенные записи и расшифровки) — пути относительно root."""
    files, lectures = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            suffix = Path(name).suffix.lower()
            if name in SKIP_FILES or name.startswith("._") or suffix in SKIP_SUFFIXES:
                continue
            if suffix in LECTURE_SUFFIXES:
                lectures.append(rel_dir / name)
                continue
            files.append(rel_dir / name)
    return files, lectures


def normalize(data: bytes) -> bytes:
    """UTF-8 без BOM и с LF. Не UTF-8 — ошибка (UnicodeDecodeError), а не догадка."""
    if data.startswith(BOM):
        data = data[len(BOM):]
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    data.decode("utf-8")
    return data


def _when(path: Path) -> tuple:
    stamp = time.localtime(max(path.stat().st_mtime, 315532800))  # не раньше 1980 года
    return stamp[:6]


def _entry(name: str, mode: int, when: tuple, is_dir: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=when)
    info.create_system = UNIX
    if is_dir:
        info.external_attr = ((stat.S_IFDIR | mode) << 16) | 0x10
    else:
        info.external_attr = (stat.S_IFREG | mode) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
    return info


def pack(root: Path, out: Path) -> tuple:
    """Собирает архив; возвращает (сколько файлов, пропущенные записи)."""
    files, lectures = collect(root)
    now = time.localtime()[:6]
    tmp = out.with_name(out.name + ".part")
    made_dirs: set = set()
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_entry(TOP + "/", 0o755, now, is_dir=True), b"")
        for rel in files:
            for i in range(1, len(rel.parts)):
                folder = "/".join(rel.parts[:i])
                if folder not in made_dirs:
                    made_dirs.add(folder)
                    zf.writestr(_entry(f"{TOP}/{folder}/", 0o755, now, is_dir=True), b"")
            src = root / rel
            data = src.read_bytes()
            if rel.suffix.lower() in TEXT_EXT:
                data = normalize(data)
            mode = 0o755 if rel.name in EXECUTABLE else 0o644
            zf.writestr(_entry(f"{TOP}/{rel.as_posix()}", mode, _when(src)), data)
    os.replace(tmp, out)
    return len(files), lectures


def verify(out: Path, required: tuple = REQUIRED, show: bool = True) -> list:
    """Открывает готовый архив и проверяет его. Возвращает список проблем."""
    say = print if show else (lambda *_a: None)
    problems = []
    with zipfile.ZipFile(out) as zf:
        broken = zf.testzip()
        if broken:
            problems.append(f"повреждён файл в архиве: {broken}")
        infos = zf.infolist()
        names = [i.filename for i in infos]
        say("Файлы в архиве:")
        for info in infos:
            if not info.is_dir():
                say(f"  {info.filename}  ({info.file_size} байт)")
        for name in names:
            parts = name.rstrip("/").split("/")
            if parts[0] != TOP:
                problems.append(f"файл вне папки {TOP}/: {name}")
            if any(p in FORBIDDEN_PARTS for p in parts):
                problems.append(f"лишняя папка в архиве: {name}")
            if parts[-1] in SKIP_FILES or Path(parts[-1]).suffix.lower() in SKIP_SUFFIXES:
                problems.append(f"лишний файл в архиве: {name}")
            if Path(parts[-1]).suffix.lower() in LECTURE_SUFFIXES:
                problems.append(f"запись или расшифровка лекции в архиве: {name}")
            if not name.isascii():
                problems.append(f"имя не латиницей: {name}")
        for rel in required:
            if f"{TOP}/{rel}" not in names:
                problems.append(f"в архиве нет нужного файла: {rel}")
        command = f"{TOP}/Lectures.command"
        if command in names:
            info = zf.getinfo(command)
            mode = (info.external_attr >> 16) & 0o777
            system = "Unix" if info.create_system == UNIX else f"код {info.create_system}"
            say(f"Права Lectures.command: {mode:o} (архив сделан как в {system})")
            if mode != 0o755 or info.create_system != UNIX:
                problems.append(f"у Lectures.command права {mode:o}, нужно 755 (Unix)")
        crlf, bom, not_utf8 = [], [], []
        for info in infos:
            if info.is_dir() or Path(info.filename).suffix.lower() not in TEXT_EXT:
                continue
            data = zf.read(info)
            if data.startswith(BOM):
                bom.append(info.filename)
            if b"\r" in data:
                crlf.append(info.filename)
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                not_utf8.append(info.filename)
        say("CRLF в текстовых файлах: " + ("нет" if not crlf else ", ".join(crlf)))
        say("BOM в текстовых файлах: " + ("нет" if not bom else ", ".join(bom)))
        problems += [f"CRLF: {n}" for n in crlf] + [f"BOM: {n}" for n in bom]
        problems += [f"не UTF-8: {n}" for n in not_utf8]
        extra = [n for n in names if any(p in FORBIDDEN_PARTS for p in n.split("/"))]
        say("Лишних папок (tmp, logs, test-root, __pycache__): " + ("нет" if not extra else ", ".join(extra)))
    return problems


def main(argv: list) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    out = PROJECT / ARCHIVE_NAME
    if out.exists():
        print(f"Прежний {ARCHIVE_NAME} будет заменён новым.")
    try:
        count, lectures = pack(PROJECT, out)
    except UnicodeDecodeError as err:
        print(f"Текстовый файл не в UTF-8 — архив не собран: {err}")
        return 1
    for rel in lectures:
        print(f"Не кладу в архив запись или расшифровку лекции: {rel.as_posix()}")
    size = out.stat().st_size
    print(f"Собран {out.name}: файлов {count}, размер {size / 1024:.0f} КБ ({size} байт).\n")
    problems = verify(out)
    if problems:
        print("\nАрхив НЕ годится:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nПроверка пройдена: архив годится для мака.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
