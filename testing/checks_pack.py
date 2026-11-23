"""Архив для мака: сборщик pack_for_mac.py на настоящем проекте и на подставной
папке с мусором (tmp/, logs/, settings.json, записи, расшифровки, CRLF и BOM)."""
from __future__ import annotations

import zipfile

import pack_for_mac as packer

from .harness import PROJECT, check, expect

BOM = bytes([0xEF, 0xBB, 0xBF])


@check("архив для мака: lecture-app/ внутри, у Lectures.command право на запуск, LF без BOM")
def check_pack_project(env) -> None:
    out = env.root / "pack" / packer.ARCHIVE_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    real = PROJECT / packer.ARCHIVE_NAME
    before = real.stat().st_mtime_ns if real.exists() else None
    count, _lectures = packer.pack(PROJECT, out)
    problems = packer.verify(out, show=False)
    expect(count >= 20 and not problems, f"файлов {count}; проблемы: {problems}")
    after = real.stat().st_mtime_ns if real.exists() else None
    expect(before == after, "проверка тронула настоящий архив")


@check("сборщик архива выбрасывает лишнее, записи и расшифровки, чинит CRLF и BOM")
def check_pack_dirty(env) -> None:
    src = env.root / "pack-src"
    content = {
        "Lectures.command": BOM + b"#!/bin/bash\r\nexec python3 app.py\r\n",
        "app.py": b"print('ok')\r\n",
        "lecapp/__init__.py": b"",
        "tmp/cards/c1/f1.m4a": b"x",
        "logs/run.log": b"x",
        "test-root/x.txt": b"x",
        "settings.json": b"{}",
        "lecapp/__pycache__/m.pyc": b"x",
        "old.zip": b"x",
        ".DS_Store": b"x",
        "lecture.m4a": b"x",
        "notes/2026-09-07 ПЭК — запись 1.srt": b"x",
    }
    for rel, data in content.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    out = env.root / "pack" / "dirty.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    count, lectures = packer.pack(src, out)
    expect(count == 3, f"в архив попало файлов: {count}")
    expect(sorted(p.name for p in lectures) == ["2026-09-07 ПЭК — запись 1.srt", "lecture.m4a"],
           f"пропущенные записи: {lectures}")
    problems = packer.verify(out, required=("Lectures.command", "app.py"), show=False)
    expect(not problems, "\n".join(problems))
    with zipfile.ZipFile(out) as zf:
        names = sorted(n for n in zf.namelist() if not n.endswith("/"))
        expect(names == ["lecture-app/Lectures.command", "lecture-app/app.py",
                         "lecture-app/lecapp/__init__.py"], f"в архиве: {names}")
        command = zf.read("lecture-app/Lectures.command")
        expect(command == b"#!/bin/bash\nexec python3 app.py\n", f"ярлык не починен: {command!r}")
        info = zf.getinfo("lecture-app/Lectures.command")
        expect((info.external_attr >> 16) & 0o777 == 0o755 and info.create_system == 3,
               "у ярлыка нет права на запуск")
        app_mode = (zf.getinfo("lecture-app/app.py").external_attr >> 16) & 0o777
        expect(app_mode == 0o644, f"права app.py: {app_mode:o}")
