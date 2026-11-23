"""Обработка одной лекции: перевод в WAV -> склейка частей -> распознавание
-> проверка на петлю -> раскладка -> уведомление.

Тяжёлая работа идёт во временной папке tmp/work/jobN с латинскими именами.
В папку лекции результат переносится в самом конце и только после успеха.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config, files, layout, media, messages, srt
from .proc import Cancelled, ProcessRunner, RunLog
from .settings import Settings, check_subject_name

WAITING = "waiting"
RUNNING = "running"
DONE = "done"
WARNING = "warning"
ERROR = "error"
CANCELLED = "cancelled"
FINISHED = (DONE, WARNING, ERROR, CANCELLED)

CONVERT_SHARE = 0.03  # доля полосы прогресса на перевод в WAV и склейку
NOTIFY_TITLE = "Расшифровка лекций"
# Текст уведомления передаётся osascript отдельными аргументами, не вставкой в скрипт.
NOTIFY_SCRIPT = ["-e", "on run argv",
                 "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                 "-e", "end run"]


@dataclass
class LectureRequest:
    subject: str
    date: str
    items: list  # layout.Item в порядке карточки
    join_parts: bool = False
    staging: Path | None = None  # папка карточки во временной папке


@dataclass
class Context:
    paths: config.Paths
    tools: config.Tools
    get_settings: Callable[[], Settings]
    tz: dt.tzinfo | None = None


class Job:
    def __init__(self, job_id: int, request: LectureRequest) -> None:
        self.id = job_id
        self.request = request
        self.lock = threading.Lock()
        self.state = WAITING
        self.step = "Ждёт своей очереди"
        self.progress = 0.0
        self.eta_until: float | None = None
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.message = ""
        self.hint = ""
        self.warnings: list = []
        self.loops: list = []
        self.lecture_dir: Path | None = None
        self.placed: list = []
        self.project_files: int | None = None
        self.log_name: str | None = None
        self.runner: ProcessRunner | None = None
        self.placing = False
        self.cancel_requested = False

    def update(self, **fields: object) -> None:
        with self.lock:
            for key, value in fields.items():
                setattr(self, key, value)

    @property
    def finished(self) -> bool:
        with self.lock:
            return self.state in FINISHED

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            elapsed = 0.0
            if self.started_at is not None:
                elapsed = (self.finished_at or now) - self.started_at
            eta = None
            if self.state == RUNNING and self.eta_until is not None:
                eta = max(0.0, self.eta_until - now)
            count = self.project_files
            return {
                "id": self.id,
                "subject": self.request.subject,
                "date": self.request.date,
                "state": self.state,
                "step": self.step,
                "percent": int(self.progress * 100),
                "elapsed": round(elapsed, 1),
                "eta": None if eta is None else round(eta),
                "message": self.message,
                "hint": self.hint,
                "warnings": list(self.warnings),
                "loops": list(self.loops),
                "lecture_dir": str(self.lecture_dir) if self.lecture_dir else None,
                "project_files": count,
                "two_uploads": bool(count and count > config.CLAUDE_UPLOAD_LIMIT),
                "log": self.log_name,
            }


class _Tracker:
    """Прогресс и оценка оставшегося времени по строкам whisper-cli."""

    def __init__(self, job: Job, done_audio: float, duration: float, total: float,
                 phase_start: float, groups_left: int) -> None:
        self.job = job
        self.done = done_audio
        self.dur = duration
        self.total = total
        self.phase_start = phase_start
        self.group_start = time.time()
        self.groups_left = groups_left
        self.frac = 0.0

    def on_line(self, _label: str, line: str) -> None:
        frac = media.progress_from_line(line, self.dur)
        if frac is not None and frac > self.frac:
            self.frac = frac
            self.publish()

    def publish(self) -> None:
        processed = self.done + self.frac * self.dur
        share = min(1.0, processed / self.total)
        now = time.time()
        elapsed = now - self.phase_start
        if share >= 0.05 and elapsed >= 15:
            eta = elapsed * (1 - share) / share
        else:
            eta = (config.ESTIMATE_OVERHEAD_S * self.groups_left
                   + config.ESTIMATE_FACTOR * (self.total - processed)
                   - (now - self.group_start))
        self.job.update(progress=CONVERT_SHARE + (1 - CONVERT_SHARE) * share,
                        eta_until=now + max(0.0, eta))


def _check_tools(tools: config.Tools) -> None:
    if not tools.ffmpeg:
        raise messages.no_ffmpeg()
    if not tools.whisper:
        raise messages.no_whisper()
    if not Path(tools.model).is_file():
        raise messages.no_model(tools.model)


def _latin_link(src: Path, dst: Path) -> Path:
    """Жёсткая ссылка с латинским именем во временной папке; не вышло — исходный путь."""
    try:
        os.link(src, dst)
        return dst
    except OSError:
        return Path(src)


def _convert_all(job: Job, ctx: Context, runner: ProcessRunner, work: Path,
                 records: list) -> list:
    wavs = []
    for n, item in enumerate(records, 1):
        runner.check()
        job.update(step=f"Готовлю запись {n} из {len(records)} к распознаванию",
                   progress=CONVERT_SHARE * (n - 1) / len(records))
        src = _latin_link(item.path, work / f"src_{n}{files.ascii_ext(item.name)}")
        dst = work / f"in_{n}.wav"
        try:
            code = runner.run(ctx.tools.ffmpeg + media.ffmpeg_convert_args(src, dst),
                              cwd=work, awake=True)
        except OSError:
            raise messages.no_ffmpeg()
        try:
            ok = code == 0 and dst.is_file() and media.wav_duration(dst) > 0
        except Exception as err:  # noqa: BLE001 — в т.ч. wave.Error и EOFError
            runner.log.write(f"WAV не читается: {err}")
            ok = False
        if not ok:
            raise messages.unreadable(item.name)
        wavs.append(dst)
    return wavs


def _make_groups(job: Job, work: Path, wavs: list, join: bool, log: RunLog) -> list:
    if join and len(wavs) > 1:
        job.update(step="Склеиваю части записи")
        joined = work / "joined.wav"
        duration = media.concat_wavs(wavs, joined)
        log.write(f"Склеено частей: {len(wavs)}, вместе {srt.ts_human(duration)}")
        for wav in wavs:
            wav.unlink()
        return [joined]
    return list(wavs)


def _recognize_all(job: Job, ctx: Context, runner: ProcessRunner, work: Path,
                   groups: list, threshold: int, log_name: str) -> list:
    durations = [media.wav_duration(w) for w in groups]
    total = sum(durations) or 1.0
    phase_start = time.time()
    done_audio = 0.0
    results = []
    for g, (wav, duration) in enumerate(zip(groups, durations), 1):
        runner.check()
        step = "Распознаю запись" if len(groups) == 1 else f"Распознаю запись {g} из {len(groups)}"
        job.update(step=step)
        tracker = _Tracker(job, done_audio, duration, total, phase_start, len(groups) - g + 1)
        tracker.publish()
        out_base = work / f"out_{g}"
        cmd = ctx.tools.whisper + media.whisper_args(ctx.tools.model, wav, out_base)
        try:
            code = runner.run(cmd, cwd=work, on_line=tracker.on_line, awake=True,
                              quiet_stdout=True)
        except OSError:
            raise messages.no_whisper()
        if code != 0:
            raise messages.whisper_failed(code, log_name)
        srt_path, txt_path = work / f"out_{g}.srt", work / f"out_{g}.txt"
        if not srt_path.is_file():
            raise messages.whisper_no_result(log_name)
        hits = srt.check_file(srt_path, threshold)
        tr = layout.Transcript(srt_path, txt_path if txt_path.is_file() else None)
        results.append((tr, hits))
        done_audio += duration
    return results


def _process(job: Job, ctx: Context, runner: ProcessRunner, work: Path,
             log: RunLog, log_name: str) -> dict:
    """Делает всю работу и возвращает итоговые поля задачи. Сама задача
    получает их только в самом конце run_job — после уборки и уведомления."""
    req = job.request
    settings = ctx.get_settings()
    subject = check_subject_name(req.subject)
    date_str = layout.check_date(req.date)
    if not req.items:
        raise messages.no_files()
    lec_dir = layout.lecture_dir(settings.study_root_path(ctx.paths.home), subject, date_str)
    records = [i for i in req.items if i.kind == files.KIND_RECORD]
    results: list = []
    if records:
        _check_tools(ctx.tools)
        work.mkdir(parents=True, exist_ok=True)
        wavs = _convert_all(job, ctx, runner, work, records)
        groups = _make_groups(job, work, wavs, req.join_parts, log)
        results = _recognize_all(job, ctx, runner, work, groups,
                                 settings.loop_threshold, log_name)

    runner.check()
    with job.lock:
        if job.cancel_requested:
            raise Cancelled()
        job.placing = True  # дальше отмена уже не прерывает: перенос идёт до конца
        job.step = "Раскладываю файлы по папке лекции"
    try:
        placed = layout.place(lec_dir, date_str, subject, [r[0] for r in results], req.items)
    except OSError as err:
        log.write(traceback.format_exc())
        raise messages.place_failed(err, log_name)
    if req.staging is not None:
        try:
            files.remove_inside(req.staging, ctx.paths.tmp)
        except (OSError, ValueError) as err:
            log.write(f"Папка карточки не убрана: {err}")

    warnings, loops = [], []
    for (srt_dst, _txt), (_tr, hits) in zip(placed.transcripts, results):
        for hit in hits:
            log.write(f"Петля в {srt_dst.name}: {srt.ts_human(hit.start)}-"
                      f"{srt.ts_human(hit.end)}, {hit.count} раз подряд: {hit.text}")
        if hits:
            first = hits[0]
            warnings.append(messages.loop_warning(srt.ts_human(first.start), first.count,
                                                  srt_dst.name))
            loops.append({"file": srt_dst.name, "start": first.start,
                          "timecode": srt.ts_human(first.start), "count": first.count})
    placed_paths = [p for pair in placed.transcripts for p in pair if p] + placed.files
    for path in placed_paths:
        log.write(f"-> {path}")
    return {
        "state": WARNING if warnings else DONE,
        "step": "Готово, но есть предупреждение" if warnings else "Готово",
        "message": "Готово, но проверьте расшифровку." if warnings else "Готово.",
        "progress": 1.0, "warnings": warnings, "loops": loops,
        "lecture_dir": lec_dir, "placed": placed_paths,
        "project_files": layout.project_file_count(lec_dir),
    }


def _notify(ctx: Context, request: LectureRequest, state: str, message: str,
            log: RunLog) -> None:
    if not ctx.tools.osascript:
        log.write("Уведомление пропущено: на этом компьютере нет osascript.")
        return
    head = f"{request.subject}, {request.date}"
    if state == DONE:
        text = f"{head}: расшифровка готова."
    elif state == WARNING:
        text = f"{head}: готово, но возможна петля — проверьте расшифровку."
    else:
        text = f"{head}: ошибка. {message}"
    cmd = ctx.tools.osascript + NOTIFY_SCRIPT + [NOTIFY_TITLE, text]
    try:
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as err:
        log.write(f"Уведомление не отправлено: {err}")
        return
    if res.returncode == 0:
        log.write("Уведомление передано системе (покажет ли его мак — решают его настройки уведомлений).")
    else:
        detail = res.stderr.decode("utf-8", "replace").strip()[-300:]
        log.write(f"Уведомление не отправлено: osascript вернул код {res.returncode}. {detail}")


def run_job(job: Job, ctx: Context) -> None:
    """Выполняет задачу целиком. Вызывается только из очереди (по одной за раз)."""
    req = job.request
    log_name = f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_job{job.id}.log"
    log = RunLog(ctx.paths.logs / log_name)
    keep_awake = ctx.tools.caffeinate + ["-i"] if ctx.tools.caffeinate else None
    runner = ProcessRunner(log, keep_awake)
    work = ctx.paths.tmp / "work" / f"job{job.id}"
    with job.lock:
        job.log_name = log_name
        job.runner = runner
        job.state = RUNNING
        job.started_at = time.time()
        job.step = "Начинаю"
        cancel_now = job.cancel_requested
    if cancel_now:
        runner.cancel()
    log.write(f"Задача {job.id}: {req.subject}, {req.date}; файлов: {len(req.items)}; "
              f"части одной записи: {'да' if req.join_parts else 'нет'}")
    has_records = any(i.kind == files.KIND_RECORD for i in req.items)
    try:
        outcome = _process(job, ctx, runner, work, log, log_name)
    except Cancelled:
        log.write("Отменено пользователем.")
        outcome = {"state": CANCELLED, "step": "Отменено",
                   "message": "Отменено. В папку лекции ничего не попало."}
    except messages.UserError as err:
        log.write("Ошибка: " + err.full())
        outcome = {"state": ERROR, "step": "Ошибка", "message": err.text, "hint": err.hint}
    except Exception as err:  # noqa: BLE001 — любую поломку показываем по-русски
        log.write("Непредвиденная ошибка:\n" + traceback.format_exc())
        user = messages.unexpected(err, log_name)
        outcome = {"state": ERROR, "step": "Ошибка", "message": user.text, "hint": user.hint}
    # Уборка и уведомление — до того, как задача покажется законченной.
    job.update(step="Завершаю")
    try:
        files.remove_inside(work, ctx.paths.tmp)
    except (OSError, ValueError) as err:
        log.write(f"Временная папка не убрана: {err}")
    if has_records and outcome["state"] != CANCELLED:
        try:
            _notify(ctx, req, outcome["state"], outcome.get("message", ""), log)
        except Exception as err:  # noqa: BLE001 — уведомление не должно ломать итог
            log.write(f"Уведомление не отправлено: {err}")
    log.write(f"Итог: {outcome['state']}")
    log.close()
    outcome.update(finished_at=time.time(), runner=None, eta_until=None)
    job.update(**outcome)
