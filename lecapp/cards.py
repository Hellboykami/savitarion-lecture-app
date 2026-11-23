"""Карточки лекций до запуска.

Файлы карточки лежат только во временной папке tmp/cards/cN с латинскими
именами; в папку учёбы до «Запустить» ничего не попадает, и карточку можно
удалить без следов. Загрузка пишется на диск кусками, не целиком в память.
"""
from __future__ import annotations

import datetime as dt
import itertools
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from . import files, layout, media, messages
from .config import Tools
from .pipeline import LectureRequest
from .settings import check_subject_name

SOURCE_MANUAL = "поправлено вручную"
MAX_UPLOAD_BYTES = 16 * 1024 ** 3
CHUNK = 1024 * 1024


@dataclass
class CardFile:
    id: int
    path: Path
    name: str
    kind: str
    size: int

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "size": self.size}


class Card:
    def __init__(self, card_id: int, staging: Path, subject: str, today: dt.date) -> None:
        self.id = card_id
        self.staging = staging
        self.subject = subject
        self.date = today.isoformat()
        self.date_source = media.SOURCE_TODAY
        self.date_manual = False
        self.join_parts = False
        self.files: list = []
        self.pending = 0  # сколько файлов ещё загружается
        self.next_file = itertools.count(1)

    def records(self) -> list:
        return [f for f in self.files if f.kind == files.KIND_RECORD]

    def to_json(self) -> dict:
        return {
            "id": self.id, "subject": self.subject, "date": self.date,
            "date_source": self.date_source, "date_manual": self.date_manual,
            "join_parts": self.join_parts, "pending": self.pending,
            "record_count": len(self.records()),
            "files": [f.to_json() for f in self.files],
        }


class CardStore:
    def __init__(self, tmp: Path, tools: Tools, tz: dt.tzinfo | None = None) -> None:
        self.tmp = tmp
        self.root = tmp / "cards"
        self.tools = tools
        self.tz = tz
        self._lock = threading.Lock()
        self._cards: dict = {}
        self._ids = itertools.count(1)

    def _get(self, card_id: int) -> Card:
        card = self._cards.get(card_id)
        if card is None:
            raise KeyError(card_id)
        return card

    def _today(self) -> dt.date:
        return dt.datetime.now(self.tz).date() if self.tz else dt.date.today()

    def _refresh_date(self, card: Card) -> None:
        """Дата подставляется сама, пока пользователь не поправил её вручную."""
        if card.date_manual:
            return
        recordings = [(f.path, f.name) for f in card.records()]
        found, source = media.choose_date(self.tools, recordings, self._today(), self.tz)
        card.date, card.date_source = found.isoformat(), source

    def list(self) -> list:
        with self._lock:
            return [card.to_json() for card in self._cards.values()]

    def get_json(self, card_id: int) -> dict:
        with self._lock:
            return self._get(card_id).to_json()

    def create(self, subject: str) -> dict:
        with self._lock:
            card_id = next(self._ids)
            card = Card(card_id, self.root / f"c{card_id}", subject, self._today())
            card.staging.mkdir(parents=True, exist_ok=True)
            self._cards[card_id] = card
            return card.to_json()

    def add_upload(self, card_id: int, raw_name: str, stream, length: int) -> dict:
        """Записывает загружаемый файл в папку карточки. Имя очищается от путей:
        файл с именем ../x всё равно ложится внутрь папки карточки."""
        name = files.safe_name(raw_name)
        if files.is_junk(name):
            return self.get_json(card_id)
        if length < 0 or length > MAX_UPLOAD_BYTES:
            raise messages.UserError(f"Файл «{name}» слишком большой.",
                                     "Разделите запись на части и загрузите их по отдельности.")
        with self._lock:
            card = self._get(card_id)
            file_id = next(card.next_file)
            final = card.staging / f"f{file_id}{files.ascii_ext(name)}"
            part = card.staging / f"f{file_id}.part"
            card.pending += 1
        try:
            written = 0
            with open(part, "xb") as fh:
                while written < length:
                    chunk = stream.read(min(CHUNK, length - written))
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
            if written != length:
                raise messages.UserError(f"Файл «{name}» загрузился не полностью.",
                                         "Перетащите его ещё раз.")
            os.replace(part, final)
        except BaseException:
            if part.exists():
                part.unlink()  # недогруженный файл — в своей временной папке
            with self._lock:
                card.pending -= 1
            raise
        with self._lock:
            card.pending -= 1
            if self._cards.get(card_id) is not card:
                final.unlink()  # карточку удалили, пока шла загрузка
                raise KeyError(card_id)
            card.files.append(CardFile(file_id, final, name, files.classify(name), written))
            self._refresh_date(card)
            return card.to_json()

    def update(self, card_id: int, patch: dict, subjects: list) -> dict:
        with self._lock:
            card = self._get(card_id)
            if "subject" in patch:
                subject = str(patch["subject"])
                if subject not in subjects:
                    raise messages.UserError("Такого предмета нет в списке.",
                                             "Выберите предмет из списка или добавьте его в настройках.")
                card.subject = subject
            if "date" in patch:
                value = str(patch["date"] or "").strip()
                if value:
                    card.date = layout.check_date(value)
                    card.date_manual, card.date_source = True, SOURCE_MANUAL
                else:
                    card.date_manual = False
            if "join_parts" in patch:
                card.join_parts = bool(patch["join_parts"])
            for file_id, kind in (patch.get("kinds") or {}).items():
                if kind not in files.KINDS:
                    raise messages.UserError("Неизвестный вид файла.", "Обновите страницу.")
                self._file(card, file_id).kind = kind
            if patch.get("move"):
                self._move(card, int(patch["move"]["file"]), int(patch["move"]["dir"]))
            if patch.get("remove") is not None:
                gone = self._file(card, patch["remove"])
                card.files.remove(gone)
                if gone.path.exists() and files.is_inside(gone.path, self.tmp):
                    gone.path.unlink()
            self._refresh_date(card)
            return card.to_json()

    @staticmethod
    def _file(card: Card, file_id: object) -> CardFile:
        for item in card.files:
            if item.id == int(file_id):  # type: ignore[arg-type]
                return item
        raise KeyError(file_id)

    @staticmethod
    def _move(card: Card, file_id: int, direction: int) -> None:
        """Сдвигает запись вверх или вниз среди записей: так задаётся порядок склейки."""
        positions = [i for i, f in enumerate(card.files) if f.kind == files.KIND_RECORD]
        current = [i for i in positions if card.files[i].id == file_id]
        if not current:
            return
        k = positions.index(current[0]) + (1 if direction > 0 else -1)
        if 0 <= k < len(positions):
            a, b = current[0], positions[k]
            card.files[a], card.files[b] = card.files[b], card.files[a]

    def delete(self, card_id: int) -> None:
        with self._lock:
            card = self._cards.pop(card_id)
        files.remove_inside(card.staging, self.tmp)

    def take(self, card_id: int) -> LectureRequest:
        """Забирает карточку в очередь: дальше её файлами распоряжается задача."""
        with self._lock:
            card = self._get(card_id)
            if card.pending:
                raise messages.UserError("Файлы ещё загружаются.", "Подождите пару секунд.")
            if not card.files:
                raise messages.no_files()
            subject = check_subject_name(card.subject)
            date = layout.check_date(card.date)
            items = [layout.Item(f.path, f.name, f.kind) for f in card.files]
            join = card.join_parts and len(card.records()) > 1
            del self._cards[card_id]
        return LectureRequest(subject=subject, date=date, items=items, join_parts=join,
                              staging=card.staging)
