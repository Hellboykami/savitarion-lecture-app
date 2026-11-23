"""Проверки частей конвейера без запуска программ: петля, .srt, имена,
порядок слайдов, неперезапись, виды файлов, настройки, склейка WAV."""
from __future__ import annotations

import json
import wave

from lecapp import config, files, media, messages, settings, srt

from .harness import check, expect, write_wav

PHRASES = [
    "Сегодня мы говорим о понятии авторского права.",
    "Да.",
    "Да.",
    "Хорошо, идём дальше.",
    "Первое: произведение должно быть результатом творческого труда.",
    "Второе — оно должно быть выражено в объективной форме.",
    "Вопросы есть?",
    "Да.",
    "Тогда перейдём к смежным правам.",
    "Исполнители, изготовители фонограмм, вещатели.",
]


def _ordinary_speech(minutes: int) -> list:
    segs, t, k = [], 0.0, 0
    while t < minutes * 60:
        text = PHRASES[k % len(PHRASES)]
        segs.append(srt.Segment(t, t + 3.5, f"{text} ({k})" if len(text) > 4 else text))
        t, k = t + 3.5, k + 1
    return segs


@check("петля: ловит одну фразу каждую секунду несколько минут и даёт таймкод начала")
def check_loop_found(env) -> None:
    segs = _ordinary_speech(33)
    start = segs[-1].end
    variants = ["Спасибо за внимание.", "спасибо за внимание", "Спасибо  за внимание!",
                "СПАСИБО ЗА ВНИМАНИЕ..."]
    for n in range(4 * 60):
        segs.append(srt.Segment(start + n, start + n + 1, variants[n % 4]))
    hits = srt.find_loops(segs, config.DEFAULT_LOOP_THRESHOLD)
    expect(len(hits) == 1, f"ожидалась одна петля, найдено {len(hits)}")
    expect(abs(hits[0].start - start) < 0.01, f"таймкод начала {hits[0].start}, ждали {start}")
    expect(hits[0].count == 240, f"повторов {hits[0].count}, ждали 240")
    expect(srt.ts_human(hits[0].start).startswith("33:"), srt.ts_human(hits[0].start))


@check("петля: молчит на обычной речи и считает порог N из настроек")
def check_loop_quiet(env) -> None:
    segs = _ordinary_speech(90)
    expect(not srt.find_loops(segs, config.DEFAULT_LOOP_THRESHOLD), "ложная тревога")
    four = [srt.Segment(i, i + 1, "Да.") for i in range(4)]
    five = [srt.Segment(i, i + 1, "Да.") for i in range(5)]
    expect(not srt.find_loops(four, 5), "4 повтора при N=5 — не петля")
    expect(len(srt.find_loops(five, 5)) == 1, "5 повторов при N=5 — петля")
    expect(not srt.find_loops(five, 6), "5 повторов при N=6 — не петля")
    blank = [srt.Segment(i, i + 1, "...") for i in range(10)]
    expect(not srt.find_loops(blank, 5), "сегменты без букв — не петля")


@check(".srt: чтение и запись, таймкоды, пропущенная пустая строка")
def check_srt_parse(env) -> None:
    text = ("1\n00:00:00,000 --> 00:00:02,500\n Первая фраза\n\n"
            "2\n00:00:02,500 --> 00:01:05,040\n Вторая\n строка\n"
            "3\n01:02:03,004 --> 01:02:04,000\n Третья\n")
    segs = srt.parse_srt(text)
    expect(len(segs) == 3, f"сегментов {len(segs)}")
    expect(segs[1].text == "Вторая строка", repr(segs[1].text))
    expect(abs(segs[1].end - 65.04) < 1e-6, str(segs[1].end))
    expect(abs(segs[2].start - 3723.004) < 1e-6, str(segs[2].start))
    again = srt.parse_srt(srt.format_srt(segs))
    expect([(s.start, s.end, s.text) for s in again] == [(s.start, s.end, s.text) for s in segs],
           "после записи и чтения сегменты изменились")
    expect(srt.ts_srt(3723.004) == "01:02:03,004", srt.ts_srt(3723.004))


@check("слайды: «photo (2)» раньше «photo (10)», имена из Телеграма по номеру")
def check_natural_sort(env) -> None:
    names = ["photo (10).jpg", "photo (2).jpg", "photo (1).jpg", "photo.jpg"]
    got = sorted(names, key=files.natural_key)
    expect(got == ["photo.jpg", "photo (1).jpg", "photo (2).jpg", "photo (10).jpg"], str(got))
    tg = [f"photo_{n}_2026-09-07_18-32-56.jpg" for n in (10, 2, 1, 11, 3)]
    got = [n.split("_")[1] for n in sorted(tg, key=files.natural_key)]
    expect(got == ["1", "2", "3", "10", "11"], str(got))


@check("имена: ../x и a\\b очищаются от путей, мусорные символы убираются")
def check_safe_names(env) -> None:
    cases = {"../x": "x", "a\\b": "b", "../../etc/passwd": "passwd", "..": files.FALLBACK_NAME,
             "/": files.FALLBACK_NAME, " .скрытый.txt ": "скрытый.txt", "a:b.md": "a-b.md",
             "C:\\Users\\x\\конспект.docx": "конспект.docx", "tab\tname.txt": "tabname.txt"}
    for raw, want in cases.items():
        got = files.safe_name(raw)
        expect(got == want, f"{raw!r} -> {got!r}, ждали {want!r}")
    long = "д" * 300 + ".pdf"
    cut = files.safe_name(long)
    expect(cut.endswith(".pdf") and len(cut.encode("utf-8")) <= files.MAX_NAME_BYTES,
           f"длинное имя: {len(cut.encode('utf-8'))} байт")


@check("неперезапись: занятое имя получает « (2)», « (3)», старые файлы не меняются")
def check_no_overwrite(env) -> None:
    dst = env.root / "no-overwrite"
    dst.mkdir(parents=True)
    (dst / "конспект.md").write_text("старый", encoding="utf-8")
    for n in (1, 2):
        src = env.paths.tmp / f"new{n}.md"
        src.write_text(f"новый {n}", encoding="utf-8")
        placed = files.move_unique(src, dst, "конспект.md")
        expect(placed.name == f"конспект ({n + 1}).md", placed.name)
        expect(not src.exists(), "исходник остался во временной папке")
    expect((dst / "конспект.md").read_text(encoding="utf-8") == "старый", "старый файл изменён")


@check("виды файлов: запись, конспекты, слайды, прочее; служебные файлы мака отсеиваются")
def check_classify(env) -> None:
    table = {"Запись.M4A": files.KIND_RECORD, "voice.ogg": files.KIND_RECORD,
             "лекция.mp4": files.KIND_RECORD, "план.md": files.KIND_NOTES,
             "a.txt": files.KIND_NOTES, "b.docx": files.KIND_NOTES, "c.pdf": files.KIND_NOTES,
             "photo (1).JPG": files.KIND_SLIDES, "IMG_1.heic": files.KIND_SLIDES,
             "таблица.xlsx": files.KIND_OTHER, "без расширения": files.KIND_OTHER}
    for name, kind in table.items():
        expect(files.classify(name) == kind, f"{name}: {files.classify(name)}, ждали {kind}")
    expect(files.is_junk(".DS_Store") and files.is_junk("._photo.jpg"), "мусор не отсеян")
    expect(not files.is_junk("photo.jpg"), "обычный файл принят за мусор")


@check("настройки: файл создаётся со стартовыми предметами, лишние поля сохраняются")
def check_settings(env) -> None:
    path = env.root / "settings-check" / "settings.json"
    first = settings.load_settings(path)
    expect(path.exists(), "settings.json не создан")
    expect(first.subject_names() == list(config.DEFAULT_SUBJECTS), str(first.subject_names()))
    expect(first.loop_threshold == config.DEFAULT_LOOP_THRESHOLD, str(first.loop_threshold))
    expect(first.study_root_path(env.home) == env.home / "study", str(first.study_root))
    data = json.loads(path.read_text(encoding="utf-8"))
    expect(isinstance(data["subjects"][0], dict), "предмет хранится не объектом")
    data["subjects"][0]["color"] = "red"
    data["future_option"] = 1
    data["loop_threshold"] = 7
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    again = settings.load_settings(path)
    settings.save_settings(path, again)
    saved = json.loads(path.read_text(encoding="utf-8"))
    expect(saved["subjects"][0].get("color") == "red", "поле предмета потерялось")
    expect(saved.get("future_option") == 1 and saved["loop_threshold"] == 7, str(saved))
    path.write_text("{испорчено", encoding="utf-8")
    try:
        settings.load_settings(path)
        expect(False, "испорченный файл принят")
    except messages.UserError as err:
        expect("settings.json" in err.text, err.text)
    expect(path.read_text(encoding="utf-8") == "{испорчено", "испорченный файл перезаписан")
    for bad in ("../ПЭК", "a/b", ".скрытый", "a\\b", ""):
        try:
            settings.check_subject_name(bad)
            expect(False, f"плохое название принято: {bad!r}")
        except messages.UserError:
            pass


@check("склейка WAV: части идут в показанном порядке, без пропусков")
def check_concat(env) -> None:
    a = write_wav(env.root / "concat" / "a.wav", 2.0, 3000)
    b = write_wav(env.root / "concat" / "b.wav", 1.0, -3000)
    out = env.root / "concat" / "ab.wav"
    duration = media.concat_wavs([a, b], out)
    expect(abs(duration - 3.0) < 1e-6, f"длительность {duration}")
    with wave.open(str(out), "rb") as w:
        w.setpos(int(1.99 * 16000))
        before = int.from_bytes(w.readframes(1), "little", signed=True)
        w.setpos(int(2.01 * 16000))
        after = int.from_bytes(w.readframes(1), "little", signed=True)
    expect(before == 3000 and after == -3000, f"порядок частей нарушен: {before}, {after}")
