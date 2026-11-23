"""Файл settings.json в папке проекта: корень учёбы, предметы, порог петли.

Предмет хранится объектом {"name": ...}, чтобы потом добавить поле без
переделки. Неизвестные поля (и у предметов, и в самом файле) сохраняются.
Испорченный файл никогда не перезаписывается молча.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import config, messages

KNOWN_KEYS = ("study_root", "subjects", "loop_threshold")


def _default_subjects() -> list:
    return [{"name": name} for name in config.DEFAULT_SUBJECTS]


@dataclass
class Settings:
    study_root: str = config.DEFAULT_STUDY_ROOT
    subjects: list = field(default_factory=_default_subjects)
    loop_threshold: int = config.DEFAULT_LOOP_THRESHOLD
    extra: dict = field(default_factory=dict)

    def subject_names(self) -> list[str]:
        return [item["name"] for item in self.subjects]

    def study_root_path(self, home: Path) -> Path:
        return config.expand_home(self.study_root, home)

    def to_json(self) -> dict:
        data = dict(self.extra)
        data["study_root"] = self.study_root
        data["subjects"] = self.subjects
        data["loop_threshold"] = self.loop_threshold
        return data


def check_subject_name(name: object) -> str:
    """Название предмета станет именем папки — проверяем, что это безопасно."""
    clean = " ".join(str(name).split())
    bad = (
        not clean
        or clean.startswith(".")
        or any(ch in clean for ch in "/\\:")
        or any(ord(ch) < 32 for ch in clean)
        or len(clean.encode("utf-8")) > 100
    )
    if bad:
        raise messages.bad_subject(clean or str(name))
    return clean


def from_json(data: object) -> Settings:
    if not isinstance(data, dict):
        raise ValueError("внутри должен быть объект с настройками")
    result = Settings()
    result.extra = {k: v for k, v in data.items() if k not in KNOWN_KEYS}
    if "study_root" in data:
        root = data["study_root"]
        if not isinstance(root, str) or not root.strip():
            raise ValueError("корень учёбы (study_root) должен быть непустой строкой")
        result.study_root = root.strip()
    if "subjects" in data:
        items = data["subjects"]
        if not isinstance(items, list):
            raise ValueError("предметы (subjects) должны быть списком")
        subjects = []
        seen = set()
        for item in items:
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict) or "name" not in item:
                raise ValueError("у каждого предмета должно быть поле name")
            item = dict(item)
            item["name"] = check_subject_name(item["name"])
            key = item["name"].casefold()
            if key not in seen:
                seen.add(key)
                subjects.append(item)
        result.subjects = subjects
    if "loop_threshold" in data:
        n = data["loop_threshold"]
        if isinstance(n, bool) or not isinstance(n, int) or not 2 <= n <= 1000:
            raise ValueError("порог проверки на петлю (loop_threshold) — целое число от 2 до 1000")
        result.loop_threshold = n
    return result


def apply_update(current: Settings, data: dict, home: Path) -> Settings:
    """Изменения со страницы: корень учёбы, названия предметов, порог петли.
    У переименованного предмета остаются только новые поля; у прежних — все."""
    result = Settings(study_root=current.study_root,
                      subjects=[dict(s) for s in current.subjects],
                      loop_threshold=current.loop_threshold, extra=dict(current.extra))
    if "study_root" in data:
        root = str(data["study_root"]).strip()
        if not root:
            raise messages.UserError("Укажите папку учёбы.", "Например: ~/study")
        if not config.expand_home(root, home).is_absolute():
            raise messages.UserError("Папка учёбы указана не полностью.",
                                     "Начните путь с ~ (домашняя папка) или с /, например ~/study.")
        result.study_root = root
    if "subjects" in data:
        names = data["subjects"]
        if not isinstance(names, list):
            raise messages.UserError("Список предметов не прочитался.", "Обновите страницу.")
        by_name = {item["name"]: item for item in current.subjects}
        subjects, seen = [], set()
        for raw in names:
            name = check_subject_name(raw)
            if name.casefold() not in seen:
                seen.add(name.casefold())
                subjects.append(dict(by_name.get(name, {"name": name})))
        if not subjects:
            raise messages.UserError("Нужен хотя бы один предмет.", "Добавьте предмет в список.")
        result.subjects = subjects
    if "loop_threshold" in data:
        try:
            n = int(data["loop_threshold"])
        except (TypeError, ValueError):
            n = 0
        if not 2 <= n <= 1000:
            raise messages.UserError("Порог проверки на петлю — целое число от 2 до 1000.",
                                     "По умолчанию 5.")
        result.loop_threshold = n
    return result


def save_settings(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(settings.to_json(), ensure_ascii=False, indent=2) + "\n"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def load_settings(path: Path) -> Settings:
    """Читает настройки; если файла нет — создаёт его с настройками по умолчанию."""
    if not path.exists():
        settings = Settings()
        save_settings(path, settings)
        return settings
    try:
        return from_json(json.loads(path.read_text(encoding="utf-8-sig")))
    except messages.UserError as err:
        raise messages.bad_settings(path.name, err.text)
    except (ValueError, OSError) as err:
        raise messages.bad_settings(path.name, err)
