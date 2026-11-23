"""Запуск приложения: один экземпляр на порту, уборка временных файлов,
страница в браузере и остановка при закрытии окна Терминала.

Закрытие окна присылает сигнал SIGHUP. whisper-cli работает в своей группе
процессов и сам этот сигнал не получит, поэтому приложение по сигналу
останавливает очередь — вместе с caffeinate и whisper-cli — и только потом
выходит. На Windows сигнала SIGHUP нет: его обработка пропускается.
"""
from __future__ import annotations

import http.client
import json
import signal
import subprocess
import sys
import threading
from typing import Callable, Optional

from . import config, jobs
from . import server as server_mod
from . import settings as settings_mod
from .messages import UserError

Printer = Callable[[str], None]


def say(text: str) -> None:
    print(text, flush=True)  # сразу в окно Терминала, без задержки в буфере


def ping(port: int, timeout: float = 2.0) -> dict | None:
    """Ответ уже работающего приложения на этом порту (или None)."""
    conn = http.client.HTTPConnection(server_mod.HOST, port, timeout=timeout)
    try:
        conn.request("GET", "/api/ping", headers={"Host": f"{server_mod.HOST}:{port}"})
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
    finally:
        conn.close()


def open_page(tools: config.Tools, url: str) -> bool:
    if not tools.open:
        return False
    try:
        res = subprocess.run(tools.open + [url], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def run(paths: config.Paths, tools: config.Tools, mode: str = "real",
        port: int = config.PORT, open_browser: bool = True,
        stop: Optional[threading.Event] = None,
        ready: Optional[Callable[[server_mod.App], None]] = None,
        out: Printer = say) -> int:
    try:
        httpd = server_mod.bind(port)
    except OSError as err:
        info = ping(port)
        if info and info.get("app") == server_mod.APP_ID and info.get("mode") == mode:
            url = f"http://{server_mod.HOST}:{port}/"
            if open_browser and open_page(tools, url):
                out("Приложение уже работает — открыла его страницу в браузере.")
            else:
                out(f"Приложение уже работает. Его страница: {url}")
            out("Это второе окно Терминала можно закрыть. Первое окно не закрывайте — "
                "в нём работает приложение.")
            return 0
        out(f"Не получается занять порт {port}: {err}.")
        out("Его заняла другая программа. Перезагрузите мак и запустите приложение снова.")
        return 1

    # Порт наш — значит, других экземпляров нет и временные файлы ничьи.
    removed = jobs.startup_cleanup(paths)
    app = server_mod.App(paths, tools, mode=mode, port=httpd.server_address[1])
    app.bound = httpd.server_address  # для проверки: адрес, который реально слушается
    httpd.app = app
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.5},
                              name="http", daemon=True)
    thread.start()
    stop = stop or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for name in ("SIGHUP", "SIGTERM", "SIGINT"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_args: stop.set())

    out(f"Расшифровка лекций работает: {app.url}")
    if mode == "demo":
        out("ДЕМО: распознавание имитируется, всё пишется в test-root/demo/.")
    out("Не закрывайте это окно, пока пользуетесь приложением: закрытие окна выключает "
        "приложение и останавливает текущую расшифровку.")
    if removed:
        out(f"Убрала временные файлы прошлого прерванного запуска: {removed}.")
    try:
        settings_mod.load_settings(paths.settings_file)
    except UserError as err:
        out(err.full())
    for problem in app.problems():
        out(f"Внимание: {problem['text']} {problem['hint']}")
    if open_browser and not open_page(tools, app.url):
        out(f"Откройте в браузере: {app.url}")
    if ready is not None:
        ready(app)
    try:
        while not stop.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    out("Останавливаю приложение…")
    app.shutdown()  # отменяет задачи и останавливает caffeinate с whisper-cli
    httpd.shutdown()
    httpd.server_close()
    out("Приложение остановлено.")
    return 0


def main(argv: list) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if argv == ["--demo"]:
        from testing import demo
        paths, tools = demo.prepare()
        return run(paths, tools, mode="demo", port=config.DEMO_PORT)
    if argv:
        print("Запуск: python3 app.py   (для проверки на ноутбуке: python3 app.py --demo)")
        return 2
    paths = config.Paths.for_project(config.PROJECT_DIR)
    return run(paths, config.real_tools(paths.home))
