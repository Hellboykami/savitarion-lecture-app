"""Запуск: повторный запуск открывает страницу, чужой порт — понятная ошибка,
закрытие окна Терминала (SIGHUP) останавливает whisper-cli, страница
ничего не грузит из интернета."""
from __future__ import annotations

import http.client
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.parse

from lecapp import config, launcher

from .checks_server import Running
from .harness import PROJECT, TESTING, Skip, check, expect, write_wav


def _new_open_calls(env, before: str) -> list:
    text = env.open_log.read_text(encoding="utf-8") if env.open_log.exists() else ""
    return [json.loads(line) for line in text[len(before):].splitlines() if line.strip()]


@check("повторный запуск при работающем приложении не падает, а открывает страницу")
def check_second_launch(env) -> None:
    srv = Running(env, "server-twice")
    try:
        marker = srv.paths.tmp / "cards" / "c99" / "f1.m4a"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"first")
        before = env.open_log.read_text(encoding="utf-8") if env.open_log.exists() else ""
        lines: list = []
        code = launcher.run(srv.paths, env.tools(), mode="test", port=srv.port, out=lines.append)
        expect(code == 0 and "уже работает" in " ".join(lines), f"второй запуск: {code} {lines}")
        url = f"http://127.0.0.1:{srv.port}/"
        expect([url] in _new_open_calls(env, before), "страница не открыта в браузере")
        expect(marker.exists(), "второй запуск убрал временные файлы первого")
        expect(srv.state()["app"] == "lecture-app", "первый экземпляр перестал отвечать")
    finally:
        srv.close()


@check("порт занят другой программой — понятная ошибка, временные файлы не тронуты")
def check_port_busy(env) -> None:
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    root = env.root / "server-busy"
    paths = config.Paths(project=root, tmp=root / "tmp", logs=root / "logs",
                         settings_file=root / "settings.json", home=env.home)
    keep = paths.tmp / "keep.txt"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("не трогать", encoding="utf-8")
    try:
        lines: list = []
        code = launcher.run(paths, env.tools(), mode="test", port=port, out=lines.append)
        expect(code == 1 and "занять порт" in " ".join(lines), f"итог: {code} {lines}")
        expect(keep.exists(), "при чужом порте убраны временные файлы")
    finally:
        blocker.close()


def _call(port: int, token: str, method: str, path: str, body: object = None) -> object:
    headers = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}",
               "X-App-Token": token}
    if isinstance(body, dict):
        body = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request(method, path, body=body, headers=headers)
        raw = conn.getresponse().read().decode("utf-8")
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    finally:
        conn.close()


@check("закрытие окна Терминала (SIGHUP) останавливает приложение вместе с whisper-cli")
def check_sighup(env) -> None:
    if not hasattr(signal, "SIGHUP"):
        raise Skip("сигнала SIGHUP на Windows нет — эта проверка идёт на маке")
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    if env.heartbeat.exists():
        env.heartbeat.unlink()
    environ = dict(os.environ, FAKE_WHISPER_MODE="slow", FAKE_WHISPER_SECONDS="60")
    cmd = [sys.executable, str(TESTING / "serve_for_test.py"), str(env.root / "server-sighup"),
           str(port), str(env.home)]
    proc = subprocess.Popen(cmd, env=environ, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 15
        while time.time() < deadline and not launcher.ping(port, timeout=0.5):
            time.sleep(0.1)
        page = _call(port, "", "GET", "/")
        token = re.search(r'name="app-token" content="([^"]+)"', str(page)).group(1)
        card = _call(port, token, "POST", "/api/cards")
        wav = write_wav(env.inputs / "sighup.wav", 1.0).read_bytes()
        _call(port, token, "POST", f"/api/cards/{card['id']}/files?name="
              + urllib.parse.quote("запись.m4a"), wav)
        _call(port, token, "POST", f"/api/cards/{card['id']}/run")
        deadline = time.time() + 15
        while time.time() < deadline and not (env.heartbeat.exists()
                                               and env.heartbeat.read_text() not in ("", "1")):
            time.sleep(0.05)
        expect(env.heartbeat.exists(), "имитация whisper не запустилась")
        proc.send_signal(signal.SIGHUP)
        code = proc.wait(25)
        out = proc.stdout.read().decode("utf-8", "replace")
        expect(code == 0 and "остановлено" in out, f"приложение: код {code}\n{out[-600:]}")
        beat = env.heartbeat.read_text()
        time.sleep(0.5)
        expect(env.heartbeat.read_text() == beat, "whisper-cli продолжает работать в фоне")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@check("остановка приложения посреди расшифровки останавливает и whisper-cli")
def check_stop_kills_job(env) -> None:
    srv = Running(env, "server-stop")
    stopped = False
    try:
        if env.heartbeat.exists():
            env.heartbeat.unlink()
        card = srv.call("POST", "/api/cards")[1]
        srv.upload(card["id"], "запись.m4a", write_wav(env.inputs / "stop.wav", 1.0).read_bytes())
        os.environ["FAKE_WHISPER_MODE"], os.environ["FAKE_WHISPER_SECONDS"] = "slow", "60"
        try:
            srv.call("POST", f"/api/cards/{card['id']}/run")
            deadline = time.time() + 15
            while time.time() < deadline and not (env.heartbeat.exists()
                                                   and env.heartbeat.read_text() not in ("", "1")):
                time.sleep(0.05)
        finally:
            os.environ.pop("FAKE_WHISPER_MODE", None)
            os.environ.pop("FAKE_WHISPER_SECONDS", None)
        expect(env.heartbeat.exists(), "имитация whisper не запустилась")
        srv.close()  # тот же путь, что и после сигнала закрытия окна
        stopped = True
        expect(not srv.thread.is_alive(), "приложение не остановилось")
        expect(any("остановлено" in line for line in srv.lines), " | ".join(srv.lines))
        beat = env.heartbeat.read_text()
        time.sleep(0.5)
        expect(env.heartbeat.read_text() == beat, "whisper-cli продолжает работать в фоне")
        expect(not list((srv.paths.tmp / "work").glob("*")), "временные файлы не убраны")
    finally:
        if not stopped:
            srv.close()


@check("страница не грузит ничего из интернета: ни шрифтов, ни скриптов")
def check_no_external(env) -> None:
    web = PROJECT / "web"
    found = []
    for path in sorted(web.iterdir()):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"https?://|//[a-z0-9-]+\.[a-z]{2,}|@import|url\(", text, re.I):
            found.append(f"{path.name}: {m.group(0)}")
    expect(not found, "внешние ссылки: " + ", ".join(found))
    index = (web / "index.html").read_text(encoding="utf-8")
    refs = set(re.findall(r'(?:src|href)="([^"]+)"', index))
    expect(refs == {"/app.css", "/app.js", "/cards.js"}, f"страница подключает: {refs}")
