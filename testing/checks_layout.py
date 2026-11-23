"""Раскладка по папке лекции, выбор даты и уборка временной папки."""
from __future__ import annotations

import datetime as dt
import subprocess

from lecapp import files, jobs, layout, media

from .harness import check, expect, write_wav

TODAY = dt.date(2026, 11, 23)
MSK = dt.timezone(dt.timedelta(hours=3))


def _item(env, raw_name: str, content: str, kind: str = "") -> layout.Item:
    """Файл во временной папке, как после загрузки. Имя — сырое: раскладка
    сама очищает его ещё раз."""
    folder = env.paths.tmp / "layout-cards"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"f{len(list(folder.iterdir())) + 1}"
    path.write_text(content, encoding="utf-8")
    return layout.Item(path, raw_name, kind or files.classify(files.safe_name(raw_name)))


def _transcript(env, text: str) -> layout.Transcript:
    folder = env.paths.tmp / "layout-out"
    folder.mkdir(parents=True, exist_ok=True)
    n = len(list(folder.iterdir())) + 1
    srt_path, txt_path = folder / f"out{n}.srt", folder / f"out{n}.txt"
    srt_path.write_text(f"1\n00:00:00,000 --> 00:00:01,000\n {text}\n", encoding="utf-8")
    txt_path.write_text(f" {text}\n", encoding="utf-8")
    return layout.Transcript(srt_path, txt_path)


def _read(path) -> str:
    return path.read_text(encoding="utf-8")


@check("раскладка: по видам, слайды по порядку, ../x и a\\b остаются в папке лекции")
def check_layout_first(env) -> None:
    root = env.study_root()
    lec = layout.lecture_dir(root, "ПЭК", "2026-09-07")
    items = [
        _item(env, "запись.m4a", "звук"),
        _item(env, "конспект.md", "конспект 1"),
        _item(env, "../x.txt", "икс"),
        _item(env, "a\\b.pdf", "бэ"),
        _item(env, "photo (10).jpg", "photo (10)"),
        _item(env, "photo (2).jpg", "photo (2)"),
        _item(env, "../../photo (1).jpg", "photo (1)"),
        _item(env, "таблица.xlsx", "таблица"),
    ]
    trs = [_transcript(env, "первая"), _transcript(env, "вторая")]
    placed = layout.place(lec, "2026-09-07", "ПЭК", trs, items)
    want = {
        "расшифровка/2026-09-07 ПЭК — запись 1.srt", "расшифровка/2026-09-07 ПЭК — запись 1.txt",
        "расшифровка/2026-09-07 ПЭК — запись 2.srt", "расшифровка/2026-09-07 ПЭК — запись 2.txt",
        "запись/запись.m4a", "конспекты/конспект.md", "конспекты/x.txt", "конспекты/b.pdf",
        "слайды/слайд-01.jpg", "слайды/слайд-02.jpg", "слайды/слайд-03.jpg",
        "прочее/таблица.xlsx",
    }
    got = {p.relative_to(lec).as_posix() for p in lec.rglob("*") if p.is_file()}
    expect(got == want, f"лишнее: {sorted(got - want)}; не хватает: {sorted(want - got)}")
    order = [_read(lec / "слайды" / f"слайд-0{k}.jpg") for k in (1, 2, 3)]
    expect(order == ["photo (1)", "photo (2)", "photo (10)"], f"порядок слайдов: {order}")
    expect(_read(lec / "расшифровка" / "2026-09-07 ПЭК — запись 2.txt").strip() == "вторая",
           "расшифровки перепутаны")
    outside = [p for p in root.rglob("*") if p.is_file() and not files.is_inside(p, lec)]
    expect(not outside, f"файлы вне папки лекции: {outside}")
    expect(len(placed.transcripts) == 2 and len(placed.files) == len(items), "не всё перенесено")
    expect(layout.project_file_count(lec) == 8, f"в проект: {layout.project_file_count(lec)}")


@check("раскладка: в существующую папку — нумерация продолжается, старое не тронуто")
def check_layout_continue(env) -> None:
    lec = layout.lecture_dir(env.study_root(), "ПЭК", "2026-09-07")
    before = {p: _read(p) for p in lec.rglob("*") if p.is_file()}
    expect(before, "нет результата предыдущей проверки")
    items = [_item(env, "photo (2).jpg", "новый 2"), _item(env, "photo (1).jpg", "новый 1"),
             _item(env, "конспект.md", "конспект 2")]
    layout.place(lec, "2026-09-07", "ПЭК", [_transcript(env, "третья")], items)
    for path, text in before.items():
        expect(path.exists() and _read(path) == text, f"изменён старый файл {path.name}")
    expect(_read(lec / "слайды" / "слайд-04.jpg") == "новый 1", "слайд-04 не тот")
    expect(_read(lec / "слайды" / "слайд-05.jpg") == "новый 2", "слайд-05 не тот")
    expect((lec / "расшифровка" / "2026-09-07 ПЭК — запись 3.srt").exists(), "нет записи 3")
    expect(_read(lec / "конспекты" / "конспект (2).md") == "конспект 2", "нет « (2)» у конспекта")
    expect(layout.next_record_number(lec) == 4 and layout.next_slide_number(lec) == 6,
           "счётчики не продолжаются")


@check("дата: из метки в файле (в местном времени), из имени ГГГГ-ММ-ДД, иначе сегодня")
def check_dates_parse(env) -> None:
    cases = [("2026-09-07T22:30:00.000000Z", MSK, dt.date(2026, 9, 8)),
             ("2026-09-08T01:10:00+03:00", dt.timezone.utc, dt.date(2026, 9, 7)),
             ("2026-09-07T18:32:56+0300", MSK, dt.date(2026, 9, 7)),
             ("2026-09-07 10:00:00", MSK, dt.date(2026, 9, 7)),
             ("2026-09-07", MSK, dt.date(2026, 9, 7)),
             ("вчера", MSK, None)]
    for value, tz, want in cases:
        got = media.parse_stamp(value, tz)
        expect(got == want, f"{value} -> {got}, ждали {want}")
    bogus = {"creation_time": "1904-01-01T00:00:00.000000Z"}
    expect(media.date_from_tags(bogus, TODAY, MSK) is None, "пустая дата 1904 принята")
    future = {"creation_time": "2030-01-01T00:00:00Z"}
    expect(media.date_from_tags(future, TODAY, MSK) is None, "дата из будущего принята")
    apple = {"creation_time": "2026-09-07T21:30:00Z",
             "com.apple.quicktime.creationdate": "2026-09-08T00:30:00+0300"}
    expect(media.date_from_tags(apple, TODAY, MSK) == dt.date(2026, 9, 8), "метка iPhone")
    expect(media.date_from_name("Запись 2026-09-07 лекция.m4a") == dt.date(2026, 9, 7), "имя")
    expect(media.date_from_name("photo_1_2026-09-31_10-00-00.jpg") is None, "31 сентября")
    expect(media.date_from_name("20260907.m4a") is None, "дата без дефисов")


@check("дата: цепочка через ffprobe — метка в файле → имя файла → сегодня")
def check_dates_chain(env) -> None:
    tools = env.tools()
    folder = env.root / "dates"
    plain = write_wav(folder / "plain.wav", 1.0)
    meta = folder / "meta.m4a"
    cmd = tools.ffmpeg + ["-nostdin", "-i", str(plain), "-metadata",
                          "creation_time=2026-09-07T22:30:00Z", "-c:a", "aac", str(meta)]
    res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True)
    expect(res.returncode == 0, "ffmpeg не сделал файл с меткой: "
           + res.stderr.decode("utf-8", "replace")[-500:])
    got = media.choose_date(tools, [(meta, "Новая запись.m4a")], TODAY, MSK)
    expect(got == (dt.date(2026, 9, 8), media.SOURCE_META), f"метка: {got}")
    named = write_wav(folder / "named.m4a", 1.0)
    got = media.choose_date(tools, [(plain, "запись.m4a"), (named, "2026-09-07 лекция.m4a")],
                            TODAY, MSK)
    expect(got == (dt.date(2026, 9, 7), media.SOURCE_NAME), f"имя: {got}")
    broken = folder / "broken.m4a"
    broken.write_bytes(b"not audio at all")
    got = media.choose_date(tools, [(broken, "запись.m4a")], TODAY, MSK)
    expect(got == (TODAY, media.SOURCE_TODAY), f"сегодня: {got}")


@check("запуск: временные файлы прерванного прогона убираются, остальное не трогается")
def check_startup_cleanup(env) -> None:
    stale = env.paths.tmp / "work" / "job99"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "in_1.wav").write_bytes(b"x")
    keep = env.root / "not-tmp.txt"
    keep.write_text("не трогать", encoding="utf-8")
    removed = jobs.startup_cleanup(env.paths)
    expect(removed >= 1, "ничего не убрано")
    expect(not any(env.paths.tmp.iterdir()), "во временной папке что-то осталось")
    expect(keep.exists(), "убран файл вне временной папки")
