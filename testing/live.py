"""Живая проверка на маке: python3 selftest.py --live <файл записи>.

Вырезает 2 минуты из середины записи и расшифровывает их настоящими ffmpeg
и whisper-cli трижды: как .m4a, как .ogg и двумя склеенными половинами.
Результат — в test-root/live/, в конце — итог и время каждого прогона.
Потом прогоняет проверку на петлю по старым расшифровкам из ~/whisper-out
(только чтение) и показывает, где она сработала, — для сведения.
"""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import time
from pathlib import Path

from lecapp import config, files, layout, media, messages, pipeline, srt
from lecapp.jobs import JobQueue
from lecapp.settings import Settings

from .harness import TEST_ROOT

CLIP_SECONDS = 120
OGG_CODECS = (["-c:a", "libopus", "-b:a", "48k"], ["-c:a", "libvorbis"],
              ["-c:a", "opus", "-strict", "-2"])
STATE_WORDS = {pipeline.DONE: "готово", pipeline.WARNING: "готово, но есть предупреждение",
               pipeline.ERROR: "ошибка", pipeline.CANCELLED: "отменено"}


def _minutes(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60} мин {seconds % 60:02d} с"


def _ffmpeg(tools: config.Tools, args: list) -> str | None:
    """Запускает ffmpeg; None — успех, иначе хвост сообщения об ошибке."""
    res = subprocess.run(tools.ffmpeg + ["-nostdin"] + args, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode == 0:
        return None
    return res.stderr.decode("utf-8", "replace").strip()[-400:]


def make_clips(tools: config.Tools, record: Path, work: Path, clip: float) -> dict:
    duration = media.probe_duration(media.probe(tools, record)) or 0.0
    start = max(0.0, duration / 2 - clip / 2) if duration > clip else 0.0
    m4a, ogg = work / "clip.m4a", work / "clip.ogg"
    error = _ffmpeg(tools, ["-ss", f"{start:.2f}", "-t", f"{clip:g}", "-i", str(record),
                            "-vn", "-c:a", "aac", "-b:a", "128k", str(m4a)])
    if error:
        raise RuntimeError(f"ffmpeg не смог вырезать кусок записи: {error}")
    for codec in OGG_CODECS:
        error = _ffmpeg(tools, ["-i", str(m4a), "-vn"] + codec + [str(ogg)])
        if error is None:
            break
        if ogg.exists():
            ogg.unlink()  # недоделанный файл в своей временной папке
    else:
        raise RuntimeError(f"ffmpeg не смог сделать .ogg: {error}")
    half = clip / 2
    halves = [work / "half1.m4a", work / "half2.m4a"]
    for args in (["-i", str(m4a), "-t", f"{half:g}"], ["-ss", f"{half:g}", "-i", str(m4a)]):
        target = halves[0] if "-t" in args else halves[1]
        error = _ffmpeg(tools, args + ["-c:a", "aac", "-b:a", "128k", str(target)])
        if error:
            raise RuntimeError(f"ffmpeg не смог разрезать кусок на половины: {error}")
    return {"start": start, "duration": duration, "m4a": m4a, "ogg": ogg, "halves": halves}


def _watch(job: pipeline.Job, timeout: float = 3600.0) -> int:
    """Ждёт конца задачи и запоминает, до скольких процентов дошла полоса,
    пока шла работа: так видно, распознаются ли строки прогресса whisper-cli."""
    peak = 0
    deadline = time.time() + timeout
    while not job.finished and time.time() < deadline:
        snap = job.snapshot()
        if snap["state"] == pipeline.RUNNING:
            peak = max(peak, snap["percent"])
        time.sleep(0.25)
    return peak


def _srt_head(snap_paths: list, lines: int = 4) -> list:
    for path in snap_paths:
        if str(path).endswith(".srt"):
            segs = srt.read_srt(Path(path))
            return [f"      {srt.ts_human(s.start)}  {s.text}" for s in segs[:lines]]
    return ["      (расшифровки нет)"]


def scan_old(folder: Path, threshold: int) -> tuple:
    """Проверка на петлю по старым расшифровкам. Только чтение."""
    print(f"\nПроверка на петлю по старым расшифровкам из {folder} (только чтение):")
    if not folder.is_dir():
        print("  папки нет — пропускаю")
        return 0, 0
    found = sorted(folder.rglob("*.srt"), key=lambda p: files.natural_key(p.name))
    flagged = 0
    for path in found:
        hits = srt.check_file(path, threshold)
        if hits:
            flagged += 1
            where = ", ".join(f"с {srt.ts_human(h.start)} ({h.count} раз подряд)" for h in hits[:3])
            print(f"  {path.name}: сработала — {where}; фраза: «{hits[0].text[:60]}»")
        else:
            print(f"  {path.name}: петель нет")
    print(f"  Итого: файлов {len(found)}, сработала в {flagged}. Это для сведения, не ошибка.")
    return len(found), flagged


def run_live(record: Path, tools: config.Tools, home: Path, out_root: Path,
             clip: float = CLIP_SECONDS, whisper_out: Path | None = None) -> int:
    if out_root.exists():
        files.remove_inside(out_root, TEST_ROOT)
    paths = config.Paths(project=out_root, tmp=out_root / "tmp", logs=out_root / "logs",
                         settings_file=out_root / "settings.json", home=home)
    work = paths.tmp / "clips"
    work.mkdir(parents=True)
    the_settings = Settings(study_root=str(out_root / "study"))
    found_date, source = media.choose_date(tools, [(record, record.name)], dt.date.today())
    print(f"Запись: {record.name}\nДата лекции по файлу: {found_date} ({source})")
    started = time.time()
    clips = make_clips(tools, record, work, clip)
    print(f"Кусок {_minutes(clip)} с {srt.ts_human(clips['start'])} вырезан за "
          f"{time.time() - started:.1f} с")
    queue = JobQueue(pipeline.Context(paths=paths, tools=tools,
                                      get_settings=lambda: the_settings))
    runs = [("как .m4a", [clips["m4a"]], False), ("как .ogg", [clips["ogg"]], False),
            ("двумя склеенными половинами", clips["halves"], True)]
    results = []
    try:
        for n, (title, sources, join) in enumerate(runs, 1):
            card = paths.tmp / "cards" / f"card{n}"
            card.mkdir(parents=True)
            items = []
            for k, src in enumerate(sources, 1):
                dst = card / f"f{k}{src.suffix}"
                shutil.copyfile(src, dst)
                items.append(layout.Item(dst, f"live-{n}-{k}{src.suffix}", files.KIND_RECORD))
            request = pipeline.LectureRequest(subject="Проверка", date=dt.date.today().isoformat(),
                                              items=items, join_parts=join, staging=card)
            print(f"\n{n}. Расшифровка {title}…")
            t0 = time.time()
            job = queue.submit(request)
            peak = _watch(job)
            took = time.time() - t0
            snap = job.snapshot()
            results.append((title, snap["state"], took))
            print(f"   {STATE_WORDS.get(snap['state'], snap['state'])} за {_minutes(took)}")
            if snap["state"] in (pipeline.DONE, pipeline.WARNING):
                if peak > 5:
                    print(f"   полоса прогресса двигалась: видела до {peak}%")
                else:
                    print("   полоса прогресса не двигалась: строки прогресса whisper-cli "
                          "не распознаны (или запись слишком короткая)")
            for warning in snap["warnings"]:
                print(f"   ! {warning}")
            if snap["state"] in (pipeline.DONE, pipeline.WARNING):
                print("\n".join(_srt_head(job.placed)))
            else:
                print(f"   {snap['message']} {snap['hint']}")
    finally:
        queue.shutdown()
    scan_old(whisper_out or home / "whisper-out", config.DEFAULT_LOOP_THRESHOLD)
    print("\nИтог живой проверки:")
    for title, state, took in results:
        print(f"  {title:30} {STATE_WORDS.get(state, state):32} {_minutes(took)}")
    print(f"Результаты — в {out_root}. Уведомлений должно было прийти {len(results)}.")
    good = len(results) == len(runs) and all(
        state in (pipeline.DONE, pipeline.WARNING) for _t, state, _s in results)
    return 0 if good else 1


def main(record_arg: str | None) -> int:
    if not config.is_mac():
        print("Живая проверка запускается только на маке: здесь нет whisper-cli и модели.")
        return 2
    if not record_arg:
        print("После --live перетащите в окно Терминала файл записи лекции и нажмите Enter.")
        return 2
    record = Path(record_arg.strip().strip("'\"")).expanduser()
    if not record.is_file():
        print(f"Не нахожу файл записи: {record}")
        return 2
    tools = config.real_tools()
    problems = []
    if not tools.ffmpeg:
        problems.append(messages.no_ffmpeg().full())
    if not tools.ffprobe:
        problems.append("Не найдена программа ffprobe — она ставится вместе с ffmpeg: "
                        "brew install ffmpeg.")
    if not tools.whisper:
        problems.append(messages.no_whisper().full())
    if not tools.model.is_file():
        problems.append(messages.no_model(tools.model).full())
    if problems:
        print("\n".join(problems))
        return 1
    return run_live(record, tools, Path.home(), TEST_ROOT / "live")
