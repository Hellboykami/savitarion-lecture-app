"""Задачи целиком через очередь, с имитацией whisper-cli: обычный режим,
петля, ошибка, склейка частей, отмена, очередь, нехватка программ, уведомления."""
from __future__ import annotations

import json
import time

from lecapp import files, layout, pipeline, srt

from .harness import check, expect, fake_mode, run_one

DATE = "2026-09-07"


def _lecture(env, subject: str, date: str = DATE):
    return layout.lecture_dir(env.study_root(), subject, date)


def _wait_until(predicate, timeout: float, what: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"не дождалась: {what}")


def _notifications(env) -> list:
    if not env.notify_log.exists():
        return []
    lines = env.notify_log.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _work_dirs(env) -> list:
    work = env.paths.tmp / "work"
    return list(work.iterdir()) if work.is_dir() else []


@check("задача: запись + конспект + слайды → расшифровка и папка лекции, «готово»")
def check_job_normal(env) -> None:
    rec = env.wav("lecture.m4a", 6.0)
    notes = env.root / "inputs" / "plan.md"
    notes.write_text("# План", encoding="utf-8")
    slide = env.root / "inputs" / "s.jpg"
    slide.write_bytes(b"jpeg")
    items = env.stage([(rec, "Запись лекции.m4a"), (notes, "План.md"), (slide, "photo (1).jpg")])
    staging = items[0].path.parent
    before = len(_notifications(env))
    date = "2026-09-28"  # своя дата: папку ПЭК/2026-09-07 уже заполнила проверка раскладки
    with fake_mode("normal"):
        job = run_one(env.queue(), env.request("ПЭК", date, items))
    snap = job.snapshot()
    expect(snap["state"] == pipeline.DONE, f"состояние {snap['state']}: {snap['message']} {snap['hint']}")
    lec = _lecture(env, "ПЭК", date)
    base = lec / "расшифровка" / f"{date} ПЭК — запись 1"
    srt_path = base.with_name(base.name + ".srt")
    expect(srt_path.exists() and base.with_name(base.name + ".txt").exists(), "нет .srt/.txt")
    segs = srt.read_srt(srt_path)
    expect(len(segs) == 3 and segs[0].text.startswith("Фраза 1"), f"сегменты: {segs[:2]}")
    expect((lec / "запись" / "Запись лекции.m4a").exists(), "исходная запись не в папке запись/")
    expect((lec / "конспекты" / "План.md").exists() and (lec / "слайды" / "слайд-01.jpg").exists(),
           "конспект или слайд не разложены")
    expect(snap["percent"] == 100 and snap["project_files"] == 3, str(snap))
    expect(not snap["two_uploads"], "подсказка про два захода не к месту")
    expect(not _work_dirs(env) and not staging.exists(), "временные файлы не убраны")
    expect((env.paths.logs / snap["log"]).exists(), "нет журнала прогона в logs/")
    calls = _notifications(env)[before:]
    expect(len(calls) == 1, f"уведомлений {len(calls)}")
    args = calls[0]
    expect(args[-2] == pipeline.NOTIFY_TITLE and "ПЭК" in args[-1] and "готова" in args[-1],
           f"уведомление: {args}")
    expect(args[:-2] == pipeline.NOTIFY_SCRIPT, "текст уведомления попал внутрь скрипта")


@check("задача: при петле вместо «готово» — предупреждение с таймкодом")
def check_job_loop(env) -> None:
    rec = env.wav("loop.m4a", 3.0)
    items = env.stage([(rec, "Голосовое.ogg")])
    with fake_mode("loop"):
        job = run_one(env.queue(), env.request("Авторское право", DATE, items))
    snap = job.snapshot()
    expect(snap["state"] == pipeline.WARNING, f"состояние {snap['state']}")
    expect(snap["message"] != "Готово.", "показано просто «готово»")
    text = " ".join(snap["warnings"])
    expect("0:20" in text and "запись 1.srt" in text, f"предупреждение: {text}")
    expect(snap["loops"] and snap["loops"][0]["count"] == 180, str(snap["loops"]))


@check("задача: сбой распознавания — понятная ошибка, в папке лекции ничего нет")
def check_job_error(env) -> None:
    rec = env.wav("err.m4a", 2.0)
    items = env.stage([(rec, "запись.m4a")])
    staging = items[0].path.parent
    before = len(_notifications(env))
    subject = 'Семинар "Право"'
    with fake_mode("error"):
        job = run_one(env.queue(), env.request(subject, DATE, items))
    snap = job.snapshot()
    expect(snap["state"] == pipeline.ERROR, f"состояние {snap['state']}")
    expect("Распознавание остановилось с ошибкой (код 3)" in snap["message"], snap["message"])
    expect("logs/" in snap["hint"], snap["hint"])
    expect(not _lecture(env, subject).exists(), "после ошибки появилась папка лекции")
    expect(not _work_dirs(env), "временные файлы не убраны")
    expect(staging.exists(), "материалы карточки пропали — повторить будет нечем")
    calls = _notifications(env)[before:]
    expect(len(calls) == 1 and "ошибка" in calls[0][-1] and subject in calls[0][-1],
           f"уведомление об ошибке: {calls}")


@check("склейка: две части — одна расшифровка, таймкоды второй продолжают первую")
def check_job_join(env) -> None:
    part1 = env.wav("part1.m4a", 6.0, level=3000)
    part2 = env.wav("part2.m4a", 4.0, level=-3000)
    items = env.stage([(part1, "Запись 1.m4a"), (part2, "Запись 2.m4a")])
    with fake_mode("normal"):
        job = run_one(env.queue(), env.request("Legal Tech", DATE, items, join=True))
    expect(job.state == pipeline.DONE, f"состояние {job.state}: {job.message}")
    tr_dir = _lecture(env, "Legal Tech") / "расшифровка"
    srts = sorted(p.name for p in tr_dir.glob("*.srt"))
    expect(srts == [f"{DATE} Legal Tech — запись 1.srt"], f"расшифровки: {srts}")
    segs = srt.read_srt(tr_dir / srts[0])
    first = [s for s in segs if s.start < 6.0]
    second = [s for s in segs if s.start >= 6.0]
    expect(first and all("плюс" in s.text for s in first), f"первая часть: {first}")
    expect(second and all("минус" in s.text for s in second), f"вторая часть: {second}")
    expect(abs(segs[-1].end - 10.0) < 0.01, f"конец {segs[-1].end}, ждали 10.0")
    expect(len(list((_lecture(env, "Legal Tech") / "запись").iterdir())) == 2, "нет обеих частей")


@check("без галочки «части одной записи» каждая запись расшифровывается отдельно")
def check_job_separate(env) -> None:
    a = env.wav("sep1.m4a", 2.0, level=3000)
    b = env.wav("sep2.m4a", 2.0, level=-3000)
    items = env.stage([(a, "a.m4a"), (b, "b.m4a")])
    with fake_mode("normal"):
        job = run_one(env.queue(), env.request("ПЭК", "2026-09-14", items))
    expect(job.state == pipeline.DONE, f"состояние {job.state}: {job.message}")
    tr_dir = _lecture(env, "ПЭК", "2026-09-14") / "расшифровка"
    srts = sorted(p.name for p in tr_dir.glob("*.srt"))
    expect(len(srts) == 2, f"расшифровки: {srts}")
    second = srt.read_srt(tr_dir / "2026-09-14 ПЭК — запись 2.srt")
    expect(second[0].start == 0.0 and "минус" in second[0].text, f"запись 2: {second[:1]}")


@check("отмена идущей: процесс остановлен, временные файлы убраны, папки лекции нет")
def check_cancel_running(env) -> None:
    rec = env.wav("slow.m4a", 2.0)
    items = env.stage([(rec, "запись.m4a")])
    queue = env.queue()
    if env.heartbeat.exists():
        env.heartbeat.unlink()
    with fake_mode("slow", seconds=30):
        job = queue.submit(env.request("ПЭК", "2026-09-21", items))
        _wait_until(lambda: env.heartbeat.exists() and env.heartbeat.read_text() not in ("", "1"),
                    15, "имитация whisper запустилась")
        proc = job.runner._proc if job.runner else None
        expect(proc is not None, "нет запущенного процесса")
        expect(queue.cancel(job.id), "отмена не принята")
        queue.wait(job.id, 15)
    expect(job.state == pipeline.CANCELLED, f"состояние {job.state}")
    expect(proc.poll() is not None, "процесс (на маке — caffeinate) не остановлен")
    beat = env.heartbeat.read_text()
    time.sleep(0.4)
    expect(env.heartbeat.read_text() == beat, "имитация whisper продолжает работать")
    expect(not _work_dirs(env), "временные файлы не убраны")
    expect(not _lecture(env, "ПЭК", "2026-09-21").exists(), "после отмены появилась папка лекции")


@check("очередь: вторая лекция ждёт первую; ожидающую можно отменить")
def check_queue(env) -> None:
    queue = env.queue()
    jobs_ = []
    with fake_mode("slow", seconds=0.8):
        for n in range(3):
            rec = env.wav(f"q{n}.m4a", 1.0)
            items = env.stage([(rec, "запись.m4a")])
            jobs_.append(queue.submit(env.request("ПЭК", f"2026-10-0{n + 1}", items)))
        first, second, third = jobs_
        _wait_until(lambda: first.state == pipeline.RUNNING, 10, "первая задача началась")
        expect(second.state == pipeline.WAITING and third.state == pipeline.WAITING,
               f"вторая и третья должны ждать: {second.state}, {third.state}")
        expect(queue.cancel(third.id), "ожидающая не отменилась")
        expect(third.state == pipeline.CANCELLED, f"третья: {third.state}")
        queue.wait(first.id, 20)
        queue.wait(second.id, 20)
    expect(first.state == pipeline.DONE and second.state == pipeline.DONE,
           f"итог: {first.state}, {second.state}")
    expect(second.started_at >= first.finished_at, "вторая началась раньше конца первой")
    expect(not _lecture(env, "ПЭК", "2026-10-03").exists(), "отменённая всё же разложена")


@check("ошибки: нет whisper-cli, ffmpeg или модели — по-русски и с подсказкой")
def check_missing_tools(env) -> None:
    rec = env.wav("tools.m4a", 1.0)
    cases = [({"whisper": None}, "whisper-cli", "brew install whisper-cpp"),
             ({"ffmpeg": None}, "ffmpeg", "brew install ffmpeg"),
             ({"model": env.root / "нет-модели.bin"}, "модель", "whisper-models")]
    for changes, word, hint in cases:
        items = env.stage([(rec, "запись.m4a")])
        job = run_one(env.queue(env.tools(**changes)), env.request("ПЭК", "2026-11-01", items))
        expect(job.state == pipeline.ERROR, f"{word}: состояние {job.state}")
        expect(word in job.message and hint in job.hint, f"{word}: {job.message} / {job.hint}")
    expect(not _lecture(env, "ПЭК", "2026-11-01").exists(), "без программ появилась папка лекции")


@check("ошибки: файл не читается — понятное сообщение с подсказкой")
def check_unreadable(env) -> None:
    bad = env.root / "inputs" / "broken.m4a"
    bad.write_bytes(b"this is not audio")
    items = env.stage([(bad, "Битая запись.m4a")])
    with fake_mode("normal"):
        job = run_one(env.queue(), env.request("ПЭК", "2026-11-02", items))
    expect(job.state == pipeline.ERROR, f"состояние {job.state}")
    expect("«Битая запись.m4a» не читается" in job.message, job.message)
    expect(not _lecture(env, "ПЭК", "2026-11-02").exists(), "появилась папка лекции")


@check("задача без записи: материалы раскладываются сразу, без распознавания")
def check_no_record(env) -> None:
    slide = env.root / "inputs" / "only.png"
    slide.write_bytes(b"png")
    items = env.stage([(slide, "photo (3).png")])
    before = len(_notifications(env))
    job = run_one(env.queue(), env.request("Семинар наставника", DATE, items))
    expect(job.state == pipeline.DONE, f"состояние {job.state}: {job.message}")
    lec = _lecture(env, "Семинар наставника")
    expect((lec / "слайды" / "слайд-01.png").exists(), "слайд не разложен")
    expect(not (lec / "расшифровка").exists(), "лишняя папка расшифровки")
    expect(len(_notifications(env)) == before, "уведомление без расшифровки")


@check("всё записано в тестовый корень, настоящие папки проекта не тронуты")
def check_test_root_only(env) -> None:
    for queue in env.queues:
        for snap in queue.jobs():
            if snap["lecture_dir"]:
                expect(files.is_inside(snap["lecture_dir"], env.root),
                       f"папка лекции вне тестового корня: {snap['lecture_dir']}")
    expect(files.is_inside(env.study_root(), env.root), "корень учёбы вне тестового корня")
    for name, existed in env.preexisting.items():
        now = (env.root.parent / name).exists()
        expect(now == existed, f"selftest тронул настоящий {name}")
