"""Имитация ffmpeg для selftest на компьютере без ffmpeg.

Понимает то, что вызывают приложение и selftest:
    -nostdin -i ВХОД -ar 16000 -ac 1 -c:a pcm_s16le ВЫХОД.wav   (перевод в WAV)
    -nostdin [-ss С] [-t Д] -i ВХОД [-vn] [-c:a кодек] [-b:a ...] [-metadata k=v] ВЫХОД
Вход — WAV с любым расширением (так тесты делают «записи»). Выход — тоже WAV,
даже если он называется .m4a или .ogg. Без -nostdin и при занятом выходе —
ошибка, как у настоящего ffmpeg без -y.
"""
from __future__ import annotations

import os
import sys
import wave

import fakewav

VALUE_OPTS = {"-i", "-ss", "-t", "-ar", "-ac", "-c:a", "-acodec", "-b:a", "-f",
              "-metadata", "-strict", "-codec:a", "-q:a", "-map"}
FLAG_OPTS = {"-nostdin", "-vn", "-y", "-n", "-hide_banner", "-nostats"}


def fail(message: str, code: int = 1) -> None:
    sys.stderr.write(f"fake-ffmpeg: {message}\n")
    sys.stderr.flush()
    sys.exit(code)


def main(argv: list) -> None:
    if len(argv) < 3:
        fail("мало аргументов")
    out, args = argv[-1], argv[:-1]
    opts: dict = {}
    flags = set()
    inputs = []
    meta: dict = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a in FLAG_OPTS:
            flags.add(a)
            i += 1
        elif a in VALUE_OPTS:
            if i + 1 >= len(args):
                fail(f"нет значения для {a}")
            value = args[i + 1]
            if a == "-i":
                inputs.append(value)
            elif a == "-metadata":
                key, _, val = value.partition("=")
                meta[key] = val
            else:
                opts[a] = value
            i += 2
        else:
            fail(f"неизвестный ключ {a}")
    if "-nostdin" not in flags:
        fail("нет -nostdin: настоящий ffmpeg мог бы ждать ответа с клавиатуры")
    if len(inputs) != 1:
        fail("имитация понимает ровно один вход")
    if out.lower().endswith(".wav"):
        need = {"-ar": "16000", "-ac": "1", "-c:a": "pcm_s16le"}
        for key, value in need.items():
            if opts.get(key) != value:
                fail(f"для WAV нужен {key} {value}, получено {opts.get(key)}")
    if os.path.exists(out) and "-y" not in flags:
        fail(f"File '{out}' already exists. Exiting.")
    src = inputs[0]
    try:
        with wave.open(src, "rb") as r:
            params = (r.getnchannels(), r.getsampwidth(), r.getframerate())
    except Exception:  # noqa: BLE001 — любой нечитаемый вход
        if os.environ.get("FAKE_FFMPEG_ANY") == "1" and os.path.isfile(src):
            # Демо: настоящая запись -> «тишина» длиной по размеру файла (5–60 с).
            seconds = max(5.0, min(60.0, os.path.getsize(src) / 32000.0))
            fakewav.write_silence(out, seconds)
            sys.stderr.write(f"fake-ffmpeg: demo {src} -> {out} ({seconds:.0f} s)\n")
            return
        fail(f"{src}: Invalid data found when processing input")
    if params != (1, 2, 16000):
        fail("имитация понимает только WAV 16 кГц, моно, 16 бит")
    start = float(opts.get("-ss", 0))
    length = float(opts["-t"]) if "-t" in opts else None
    fakewav.copy_frames(src, out, start, length)
    tags = fakewav.read_tags(src)
    tags.update(meta)
    fakewav.append_tags(out, tags)
    sys.stderr.write(f"fake-ffmpeg: {src} -> {out}\n")


if __name__ == "__main__":
    main(sys.argv[1:])
