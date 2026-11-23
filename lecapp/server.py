"""Веб-сервер приложения: страница и API. Слушает только 127.0.0.1.

Защита:
  * заголовок Host — только адрес приложения (иначе отказ);
  * запросы, которые что-то меняют (все POST), принимаются только с Origin
    своей страницы и со служебным заголовком X-App-Token — его значение
    случайное при каждом запуске и известно только самой странице;
  * Content-Security-Policy запрещает странице грузить что-либо извне.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import socketserver
import subprocess
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, files, messages
from . import settings as settings_mod
from .cards import CardStore
from .jobs import JobQueue
from .pipeline import Context

HOST = "127.0.0.1"
APP_ID = "lecture-app"
TOKEN_HEADER = "X-App-Token"
WEB_DIR = config.PROJECT_DIR / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/cards.js": ("cards.js", "text/javascript; charset=utf-8"),
}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'none'")
MAX_JSON = 1024 * 1024


class App:
    """Работающее приложение: настройки, карточки, очередь."""

    def __init__(self, paths: config.Paths, tools: config.Tools, mode: str, port: int) -> None:
        self.paths = paths
        self.tools = tools
        self.mode = mode
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.cards = CardStore(paths.tmp, tools)
        self.queue = JobQueue(Context(paths=paths, tools=tools, get_settings=self.settings))
        self._settings_lock = threading.Lock()

    @property
    def host_header(self) -> str:
        return f"{HOST}:{self.port}"

    @property
    def origin(self) -> str:
        return f"http://{HOST}:{self.port}"

    @property
    def url(self) -> str:
        return self.origin + "/"

    def settings(self) -> settings_mod.Settings:
        return settings_mod.load_settings(self.paths.settings_file)

    def problems(self) -> list:
        """Чего не хватает для распознавания — показывается вверху страницы."""
        found = []
        if not self.tools.whisper:
            found.append(messages.no_whisper())
        if not self.tools.ffmpeg:
            found.append(messages.no_ffmpeg())
        if not self.tools.model.is_file():
            found.append(messages.no_model(self.tools.model))
        return [{"text": e.text, "hint": e.hint} for e in found]

    def settings_json(self) -> tuple:
        try:
            s = self.settings()
            error = None
        except messages.UserError as err:
            s, error = settings_mod.Settings(), err.full()
        data = {"study_root": s.study_root, "subjects": s.subject_names(),
                "loop_threshold": s.loop_threshold,
                "study_root_full": str(s.study_root_path(self.paths.home))}
        return data, error

    def state(self) -> dict:
        settings_data, settings_error = self.settings_json()
        return {
            "app": APP_ID, "mode": self.mode, "is_mac": config.is_mac(),
            "problems": self.problems(), "settings": settings_data,
            "settings_error": settings_error, "cards": self.cards.list(),
            "jobs": self.queue.jobs(), "upload_limit": config.CLAUDE_UPLOAD_LIMIT,
            "can_open": bool(self.tools.open),
        }

    def save_settings(self, data: dict) -> dict:
        with self._settings_lock:
            try:
                current = self.settings()
            except messages.UserError:
                current = settings_mod.Settings()  # файл испорчен; пользователь сам сохраняет новый
            new = settings_mod.apply_update(current, data, self.paths.home)
            settings_mod.save_settings(self.paths.settings_file, new)
        return self.settings_json()[0]

    def run_card(self, card_id: int) -> dict:
        names = self.settings().subject_names()
        card = self.cards.get_json(card_id)
        if card["subject"] not in names:
            raise messages.UserError("Такого предмета нет в списке.",
                                     "Выберите предмет из списка или добавьте его в настройках.")
        job = self.queue.submit(self.cards.take(card_id))
        return job.snapshot()

    def open_folder(self, job_id: int) -> dict:
        job = self.queue.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if not job.lecture_dir:
            raise messages.UserError("Папки лекции ещё нет.", "Дождитесь конца расшифровки.")
        if not self.tools.open:
            return {"ok": False,
                    "message": f"Открыть папку можно только на маке. Папка: {job.lecture_dir}"}
        subprocess.run(self.tools.open + [str(job.lecture_dir)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        return {"ok": True}

    def dismiss(self, job_id: int) -> dict:
        job = self.queue.forget(job_id)
        staging = job.request.staging
        if staging is not None and staging.exists():
            files.remove_inside(staging, self.paths.tmp)
        return {"ok": True}

    def shutdown(self) -> None:
        self.queue.shutdown()


class Handler(BaseHTTPRequestHandler):
    server_version = "LectureApp"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return  # не засорять окно Терминала строками о каждом запросе

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    # --- ответы ---------------------------------------------------------------

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: object) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, text: str, hint: str = "") -> None:
        self._json(status, {"error": text, "hint": hint})

    # --- проверки ---------------------------------------------------------------

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") == self.app.host_header

    def _refusal(self) -> str | None:
        """Почему запрос на изменение отклонён (None — всё в порядке)."""
        origin = self.headers.get("Origin")
        if origin is None:
            return "нет заголовка Origin"
        if origin != self.app.origin:
            return "запрос пришёл не со страницы приложения"
        token = self.headers.get(TOKEN_HEADER, "").encode("utf-8", "replace")
        if not hmac.compare_digest(token, self.app.token.encode("ascii")):
            return "нет служебного заголовка страницы"
        return None

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_JSON:
            raise messages.UserError("Слишком большой запрос.", "Обновите страницу.")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise messages.UserError("Запрос не прочитался.", "Обновите страницу.")
        return data

    # --- GET --------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._send(403, ("Неверный адрес. Откройте приложение по адресу "
                             f"{self.app.url}").encode("utf-8"), "text/plain; charset=utf-8")
            return
        path = urllib.parse.urlsplit(self.path).path
        if path in STATIC:
            name, ctype = STATIC[path]
            body = (WEB_DIR / name).read_bytes()
            if name == "index.html":
                body = (body.replace(b"__APP_TOKEN__", self.app.token.encode("ascii"))
                        .replace(b"__APP_MODE__", self.app.mode.encode("ascii")))
            self._send(200, body, ctype)
        elif path == "/api/state":
            self._json(200, self.app.state())
        elif path == "/api/ping":
            self._json(200, {"app": APP_ID, "mode": self.app.mode})
        else:
            self._error(404, "Нет такой страницы.")

    # --- POST: всё, что что-то меняет ---------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._error(403, "Неверный адрес.", f"Откройте приложение по адресу {self.app.url}")
            return
        reason = self._refusal()
        if reason:
            self._error(403, f"Запрос отклонён: {reason}.", "Обновите страницу приложения.")
            return
        try:
            self._route_post()
        except messages.UserError as err:
            self._error(400, err.text, err.hint)
        except (KeyError, ValueError):
            self._error(404, "Не нашла то, о чём запрос.", "Обновите страницу.")
        except Exception:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            self._error(500, "Внутренняя ошибка приложения.",
                        "Перезапустите приложение. Если повторится — принесите в Claude Code "
                        "вывод Терминала.")

    def _route_post(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        app = self.app
        if parts == ["api", "cards"]:
            subjects = app.settings().subject_names()
            self._json(200, app.cards.create(subjects[0] if subjects else ""))
        elif len(parts) == 4 and parts[:2] == ["api", "cards"] and parts[3] == "files":
            query = urllib.parse.parse_qs(url.query)
            name = (query.get("name") or [""])[0]
            length = self.headers.get("Content-Length")
            if length is None:
                raise messages.UserError("Файл пришёл без размера.", "Перетащите его ещё раз.")
            self._json(200, app.cards.add_upload(int(parts[2]), name, self.rfile, int(length)))
        elif len(parts) == 3 and parts[:2] == ["api", "cards"]:
            card = app.cards.update(int(parts[2]), self._read_json(),
                                    app.settings().subject_names())
            self._json(200, card)
        elif len(parts) == 4 and parts[:2] == ["api", "cards"] and parts[3] == "delete":
            app.cards.delete(int(parts[2]))
            self._json(200, {"ok": True})
        elif len(parts) == 4 and parts[:2] == ["api", "cards"] and parts[3] == "run":
            self._json(200, app.run_card(int(parts[2])))
        elif len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
            ok = app.queue.cancel(int(parts[2]))
            self._json(200, {"ok": ok, "message": "" if ok else
                             "Отменить уже нельзя: файлы переносятся в папку лекции."})
        elif len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "open":
            self._json(200, app.open_folder(int(parts[2])))
        elif len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "dismiss":
            self._json(200, app.dismiss(int(parts[2])))
        elif parts == ["api", "settings"]:
            self._json(200, app.save_settings(self._read_json()))
        else:
            self._error(404, "Нет такого действия.")

    def do_PUT(self) -> None:  # noqa: N802
        self._error(405, "Метод не поддерживается.")

    do_DELETE = do_PUT
    do_PATCH = do_PUT


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # На Windows этот флаг позволил бы второму серверу занять тот же порт —
    # тогда повторный запуск не нашёл бы первый. На маке он нужен для быстрого
    # перезапуска.
    allow_reuse_address = os.name != "nt"
    app: App | None = None

    def server_bind(self) -> None:
        # Без socket.getfqdn(): имя компьютера не нужно и не должно искаться в сети.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = HOST, self.server_address[1]


def bind(port: int) -> Server:
    """Занимает порт только на 127.0.0.1 — с других компьютеров приложение не видно."""
    return Server((HOST, port), Handler)
