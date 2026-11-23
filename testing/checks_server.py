"""Проверки сервера: только 127.0.0.1, чужой Host, Origin и служебный заголовок,
загрузка ../x внутри папки, карточка до запуска без следов, настройки, отмена."""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time

from lecapp import config, files, launcher

from .harness import check, expect, fake_mode, write_wav


class Running:
    """Сервер приложения в фоне на свободном порту — как после двойного щелчка."""

    def __init__(self, env, name: str = "server") -> None:
        root = env.root / name
        # Своя домашняя папка (и папка учёбы) у каждого сервера: проверки не мешают друг другу.
        self.paths = config.Paths(project=root, tmp=root / "tmp", logs=root / "logs",
                                  settings_file=root / "settings.json", home=root / "home")
        self.stop = threading.Event()
        ready = threading.Event()
        self.app = None
        self.lines: list = []

        def on_ready(app) -> None:
            self.app = app
            ready.set()

        def target() -> None:
            launcher.run(self.paths, env.tools(), mode="test", port=0, open_browser=False,
                         stop=self.stop, ready=on_ready, out=self.lines.append)

        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()
        if not ready.wait(15):
            raise AssertionError("сервер не запустился: " + " | ".join(self.lines))
        self.port = self.app.port

    def close(self) -> None:
        self.stop.set()
        self.thread.join(20)

    def call(self, method: str, path: str, body: object = None, host: str | None = None,
             origin: str | None = "own", token: str | None = "own") -> tuple:
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if origin:
            headers["Origin"] = self.app.origin if origin == "own" else origin
        if token:
            headers["X-App-Token"] = self.app.token if token == "own" else token
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            try:
                data = json.loads(raw.decode("utf-8"))
            except ValueError:
                data = raw.decode("utf-8", "replace")
            return resp.status, data, dict(resp.getheaders())
        finally:
            conn.close()

    def upload(self, card_id: int, name: str, data: bytes) -> tuple:
        from urllib.parse import quote
        return self.call("POST", f"/api/cards/{card_id}/files?name={quote(name)}", body=data)

    def state(self) -> dict:
        return self.call("GET", "/api/state", origin=None, token=None)[1]

    def wait_job(self, job_id: int, timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for job in self.state()["jobs"]:
                if job["id"] == job_id and job["state"] not in ("waiting", "running"):
                    return job
            time.sleep(0.1)
        raise AssertionError(f"задача {job_id} не закончилась")


def _lan_ip() -> str | None:
    """Адрес этого компьютера в сети, без отправки чего-либо наружу."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        ip = probe.getsockname()[0]
        probe.close()
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def _answers_as_app(ip: str, port: int) -> bool:
    """Отвечает ли по этому адресу именно наше приложение. Принять соединение
    может и чужая программа — например, VPN или прокси, у которых адрес
    198.18.0.1: это ещё не значит, что виден наш сервер."""
    conn = http.client.HTTPConnection(ip, port, timeout=3)
    try:
        conn.request("GET", "/api/ping", headers={"Host": f"127.0.0.1:{port}"})
        return b"lecture-app" in conn.getresponse().read(4096)
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def _stranger(listener: socket.socket) -> None:
    """Чужая программа, которая принимает любое соединение и отвечает не нами."""
    while True:
        try:
            conn, _addr = listener.accept()
        except OSError:
            return
        try:
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        except OSError:
            pass
        finally:
            conn.close()


@check("сервер слушает только 127.0.0.1 — по сетевому адресу его не видно")
def check_local_only(env) -> None:
    srv = Running(env)
    stranger = socket.socket()
    try:
        expect(srv.app.bound[0] == "127.0.0.1", f"слушает {srv.app.bound}")
        expect(_answers_as_app("127.0.0.1", srv.port), "проверка не узнала собственный сервер")
        # Чужой приём соединения (как у VPN) не должен приниматься за наш сервер.
        stranger.bind(("127.0.0.1", 0))
        stranger.listen(5)
        threading.Thread(target=_stranger, args=(stranger,), daemon=True).start()
        expect(not _answers_as_app("127.0.0.1", stranger.getsockname()[1]),
               "проверка приняла чужую программу за наш сервер")
        ip = _lan_ip()
        if ip:
            expect(not _answers_as_app(ip, srv.port), f"сервер отвечает по сетевому адресу {ip}")
    finally:
        stranger.close()
        srv.close()


@check("сервер: чужой Host — отказ; страница без внешних ресурсов (CSP)")
def check_host(env) -> None:
    srv = Running(env)
    try:
        status, page, headers = srv.call("GET", "/", origin=None, token=None)
        expect(status == 200 and srv.app.token in page, f"страница: {status}")
        expect("default-src 'self'" in headers.get("Content-Security-Policy", ""), "нет CSP")
        for host in ("evil.example", f"localhost:{srv.port}", f"127.0.0.1:{srv.port + 1}"):
            status, _, _ = srv.call("GET", "/api/state", host=host, origin=None, token=None)
            expect(status == 403, f"Host {host}: {status}")
        status, _, _ = srv.call("POST", "/api/cards", host="evil.example")
        expect(status == 403, f"POST с чужим Host: {status}")
    finally:
        srv.close()


@check("сервер: изменение без Origin, с чужим Origin или без служебного заголовка — отказ")
def check_origin_token(env) -> None:
    srv = Running(env)
    try:
        refused = [("без Origin", {"origin": None}),
                   ("чужой Origin", {"origin": "http://evil.example"}),
                   ("Origin null", {"origin": "null"}),
                   ("без служебного заголовка", {"token": None}),
                   ("неверный служебный заголовок", {"token": "wrong"})]
        for what, kwargs in refused:
            status, data, _ = srv.call("POST", "/api/cards", **kwargs)
            expect(status == 403, f"{what}: {status} {data}")
        expect(srv.state()["cards"] == [], "отклонённый запрос всё же создал карточку")
        status, card, _ = srv.call("POST", "/api/cards")
        expect(status == 200 and card["id"], f"свой запрос: {status} {card}")
        status, _, _ = srv.upload(card["id"], "a.txt", b"x")
        expect(status == 200, f"загрузка со своей страницы: {status}")
    finally:
        srv.close()


@check("загрузка файла с именем ../x остаётся внутри папки карточки, затем — папки лекции")
def check_upload_traversal(env) -> None:
    srv = Running(env, "server-traversal")
    try:
        card = srv.call("POST", "/api/cards")[1]
        for name in ("../x.txt", "..\\..\\y.md", "/etc/z.pdf"):
            status, data, _ = srv.upload(card["id"], name, b"text")
            expect(status == 200, f"{name}: {status} {data}")
        names = [f["name"] for f in srv.state()["cards"][0]["files"]]
        expect(names == ["x.txt", "y.md", "z.pdf"], f"имена: {names}")
        staging = srv.paths.tmp / "cards" / f"c{card['id']}"
        stray = [p for p in srv.paths.project.rglob("*")
                 if p.is_file() and p.suffix in (".txt", ".md", ".pdf")
                 and not files.is_inside(p, staging)]
        expect(not stray, f"файлы вне папки карточки: {stray}")
        job = srv.call("POST", f"/api/cards/{card['id']}/run")[1]
        result = srv.wait_job(job["id"])
        expect(result["state"] == "done", f"задача: {result}")
        lec = result["lecture_dir"]
        found = [p for p in srv.paths.project.rglob("*") if p.name in ("x.txt", "y.md", "z.pdf")]
        expect(len(found) == 3 and all(files.is_inside(p, lec) for p in found),
               f"файлы после раскладки: {found}")
    finally:
        srv.close()


@check("карточка: дата из имени, смена вида и порядка, склейка, запуск, открыть папку")
def check_card_flow(env) -> None:
    srv = Running(env, "server-flow")
    try:
        card = srv.call("POST", "/api/cards")[1]
        cid = card["id"]
        part1 = write_wav(env.inputs / "p1.wav", 2.0, 3000).read_bytes()
        part2 = write_wav(env.inputs / "p2.wav", 2.0, -3000).read_bytes()
        srv.upload(cid, "часть 2.m4a", part2)
        card = srv.upload(cid, "2026-09-07 часть 1.m4a", part1)[1]
        srv.upload(cid, "план.md", "# план".encode("utf-8"))
        expect(card["date"] == "2026-09-07" and card["date_source"] == "дата в имени файла",
               f"дата: {card['date']} ({card['date_source']})")
        files_ = srv.state()["cards"][0]["files"]
        first = files_[0]["id"]
        plan = files_[2]["id"]
        card = srv.call("POST", f"/api/cards/{cid}", {"move": {"file": first, "dir": 1},
                                                      "kinds": {str(plan): "other"},
                                                      "join_parts": True})[1]
        expect([f["name"] for f in card["files"]][:2] == ["2026-09-07 часть 1.m4a", "часть 2.m4a"],
               f"порядок: {[f['name'] for f in card['files']]}")
        expect(card["files"][2]["kind"] == "other" and card["join_parts"], str(card))
        card = srv.call("POST", f"/api/cards/{cid}", {"date": "2026-09-08"})[1]
        expect(card["date_source"] == "поправлено вручную", card["date_source"])
        card = srv.call("POST", f"/api/cards/{cid}", {"date": ""})[1]
        expect(card["date"] == "2026-09-07", f"дата не вернулась: {card['date']}")
        status, bad, _ = srv.call("POST", f"/api/cards/{cid}", {"date": "07.09.2026"})
        expect(status == 400 and "ГГГГ-ММ-ДД" in bad.get("hint", ""), f"плохая дата: {bad}")
        with fake_mode("normal"):
            job = srv.call("POST", f"/api/cards/{cid}/run")[1]
            result = srv.wait_job(job["id"])
        expect(result["state"] == "done" and result["project_files"] == 1, f"итог: {result}")
        expect(srv.state()["cards"] == [], "карточка осталась после запуска")
        before = env.open_log.read_text(encoding="utf-8") if env.open_log.exists() else ""
        status, res, _ = srv.call("POST", f"/api/jobs/{job['id']}/open")
        after = env.open_log.read_text(encoding="utf-8")[len(before):]
        calls = [json.loads(line) for line in after.splitlines() if line.strip()]
        expect(status == 200 and res["ok"] and [result["lecture_dir"]] in calls,
               f"«Открыть папку» не вызвало open с папкой лекции: {calls}")
        srv.call("POST", f"/api/jobs/{job['id']}/dismiss")
        expect(not srv.state()["jobs"], "задача не убралась из списка")
    finally:
        srv.close()


@check("карточка до «Запустить»: удаление не оставляет следов, в папке учёбы пусто")
def check_card_delete(env) -> None:
    srv = Running(env, "server-delete")
    try:
        card = srv.call("POST", "/api/cards")[1]
        srv.upload(card["id"], "запись.m4a", write_wav(env.inputs / "d.wav", 1.0).read_bytes())
        staging = srv.paths.tmp / "cards" / f"c{card['id']}"
        expect(staging.exists(), "нет папки карточки во временной папке")
        study = srv.app.settings().study_root_path(srv.paths.home)
        before = set(study.rglob("*")) if study.exists() else set()
        status, _, _ = srv.call("POST", f"/api/cards/{card['id']}/delete")
        expect(status == 200 and not staging.exists(), "папка карточки не удалена")
        after = set(study.rglob("*")) if study.exists() else set()
        expect(after == before, "в папке учёбы что-то появилось")
        expect(srv.state()["cards"] == [], "карточка осталась в списке")
    finally:
        srv.close()


@check("настройки со страницы: сохраняются, лишние поля предмета не теряются, ошибки по-русски")
def check_server_settings(env) -> None:
    srv = Running(env, "server-settings")
    try:
        srv.state()  # создаёт settings.json
        data = json.loads(srv.paths.settings_file.read_text(encoding="utf-8"))
        data["subjects"][0]["color"] = "red"
        srv.paths.settings_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        status, saved, _ = srv.call("POST", "/api/settings", {
            "study_root": "~/study2", "subjects": ["ПЭК", "Новый предмет"], "loop_threshold": 7})
        expect(status == 200 and saved["subjects"] == ["ПЭК", "Новый предмет"], f"{status} {saved}")
        on_disk = json.loads(srv.paths.settings_file.read_text(encoding="utf-8"))
        expect(on_disk["subjects"][0].get("color") == "red", "поле предмета потерялось")
        expect(on_disk["loop_threshold"] == 7 and on_disk["study_root"] == "~/study2", str(on_disk))
        for body, word in (({"subjects": ["../x"]}, "Название предмета"),
                           ({"loop_threshold": 1}, "Порог"),
                           ({"study_root": "relative/path"}, "Папка учёбы")):
            status, err, _ = srv.call("POST", "/api/settings", body)
            expect(status == 400 and word in err.get("error", ""), f"{body}: {status} {err}")
    finally:
        srv.close()


@check("очередь со страницы: отмена идущей расшифровки")
def check_server_cancel(env) -> None:
    srv = Running(env, "server-cancel")
    try:
        card = srv.call("POST", "/api/cards")[1]
        srv.upload(card["id"], "запись.m4a", write_wav(env.inputs / "c.wav", 1.0).read_bytes())
        with fake_mode("slow", seconds=30):
            job = srv.call("POST", f"/api/cards/{card['id']}/run")[1]
            deadline = time.time() + 15
            while time.time() < deadline:
                states = {j["id"]: j for j in srv.state()["jobs"]}
                if states[job["id"]]["state"] == "running" and states[job["id"]]["percent"] > 2:
                    break
                time.sleep(0.1)
            status, res, _ = srv.call("POST", f"/api/jobs/{job['id']}/cancel")
            expect(status == 200 and res["ok"], f"отмена: {status} {res}")
            result = srv.wait_job(job["id"])
        expect(result["state"] == "cancelled", f"итог: {result['state']}")
        expect(not list((srv.paths.tmp / "work").glob("*")), "временные файлы не убраны")
    finally:
        srv.close()
