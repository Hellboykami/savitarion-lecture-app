"""Очередь лекций: строго одна задача (и одно распознавание) за раз,
остальные ждут. Ожидающую можно отменить сразу, текущую — с остановкой
процесса и уборкой временных файлов. shutdown() останавливает всё:
его вызывают при закрытии окна Терминала, чтобы whisper-cli не остался в фоне.
"""
from __future__ import annotations

import itertools
import threading
import time

from . import config, files
from .pipeline import (CANCELLED, RUNNING, WAITING, Context, Job, LectureRequest,
                       run_job)


def startup_cleanup(paths: config.Paths) -> int:
    """Убирает временные файлы, оставшиеся от прерванного прогона."""
    return files.clear_dir(paths.tmp)


class JobQueue:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self._cond = threading.Condition()
        self._jobs: list = []
        self._ids = itertools.count(1)
        self._stopping = False
        self._worker = threading.Thread(target=self._loop, name="lecture-queue", daemon=True)
        self._worker.start()

    # --- для страницы -------------------------------------------------------

    def submit(self, request: LectureRequest) -> Job:
        with self._cond:
            if self._stopping:
                raise RuntimeError("очередь остановлена")
            job = Job(next(self._ids), request)
            self._jobs.append(job)
            self._cond.notify_all()
            return job

    def get(self, job_id: int) -> Job | None:
        with self._cond:
            for job in self._jobs:
                if job.id == job_id:
                    return job
        return None

    def jobs(self) -> list:
        with self._cond:
            current = list(self._jobs)
        return [job.snapshot() for job in current]

    def cancel(self, job_id: int) -> bool:
        """True — отменено (или отмена началась). False — отменять нечего или поздно:
        файлы уже переносятся в папку лекции."""
        with self._cond:
            job = None
            for candidate in self._jobs:
                if candidate.id == job_id:
                    job = candidate
            if job is None:
                return False
            with job.lock:
                if job.state == WAITING:
                    job.state = CANCELLED
                    job.step = "Отменено"
                    job.message = "Отменено до начала. В папку лекции ничего не попало."
                    job.finished_at = time.time()
                    self._cond.notify_all()
                    return True
                if job.state != RUNNING or job.placing:
                    return False
                job.cancel_requested = True
                runner = job.runner
        if runner is not None:
            runner.cancel()
        return True

    def forget(self, job_id: int) -> Job:
        """Убирает законченную задачу из списка. KeyError — нет такой или ещё не закончена."""
        with self._cond:
            for job in self._jobs:
                if job.id == job_id and job.finished:
                    self._jobs.remove(job)
                    return job
        raise KeyError(job_id)

    def wait(self, job_id: int, timeout: float | None = None) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        deadline = None if timeout is None else time.time() + timeout
        with self._cond:
            while not job.finished:
                pause = 0.5
                if deadline is not None:
                    left = deadline - time.time()
                    if left <= 0:
                        raise TimeoutError(f"задача {job_id} не закончилась за {timeout} с")
                    pause = min(pause, left)
                self._cond.wait(pause)
        return job

    def shutdown(self, timeout: float = 15.0) -> None:
        """Отменяет ожидающие и прерывает текущую задачу вместе с её процессами."""
        with self._cond:
            self._stopping = True
            ids = [job.id for job in self._jobs]
        for job_id in ids:
            self.cancel(job_id)
        with self._cond:
            self._cond.notify_all()
        if self._worker.is_alive():
            self._worker.join(timeout)

    # --- рабочий поток --------------------------------------------------------

    def _next_waiting(self) -> Job | None:
        for job in self._jobs:
            if job.state == WAITING:
                return job
        return None

    def _loop(self) -> None:
        while True:
            with self._cond:
                job = self._next_waiting()
                while job is None and not self._stopping:
                    self._cond.wait()
                    job = self._next_waiting()
                if job is None:
                    return
                with job.lock:
                    job.state = RUNNING  # под общим замком: cancel() не перехватит её как ожидающую
                    job.started_at = time.time()
                    job.step = "Начинаю"
            try:
                run_job(job, self.ctx)
            except Exception:  # noqa: BLE001 — run_job сам всё ловит; поток не должен умереть
                job.update(state="error", step="Ошибка", finished_at=time.time())
            with self._cond:
                self._cond.notify_all()
