"""Имитация whisper-cli для selftest. Запускается через тот же Python:
    python fake_whisper.py -m МОДЕЛЬ -f ВХОД.wav -l ru -mc 0 -otxt -osrt -of ВЫХОД -pp

Проверяет, что получены -l ru и -mc 0, нет -nf, есть -otxt и -osrt.
Печатает строки прогресса (stderr) и сегменты (stdout), пишет ВЫХОД.srt и .txt.

Режим — переменная окружения FAKE_WHISPER_MODE:
    normal — сегменты по 2 секунды; текст говорит знак сигнала («плюс»/«минус»/
             «ноль»), так тест склейки видит, где какая часть;
    loop   — после 20 секунд обычной речи одна фраза каждую секунду 3 минуты;
    error  — падает с кодом 3, ничего не записав;
    slow   — работает FAKE_WHISPER_SECONDS секунд (по умолчанию 30) и пишет
             «пульс» в файл FAKE_WHISPER_HEARTBEAT — для проверки отмены.
"""
from __future__ import annotations

import array
import os
import sys
import time
import wave

VALUE_OPTS = {"-m", "-f", "-l", "-mc", "-of"}
LOOP_PHRASES = ["Продолжение следует.", "продолжение следует", "Продолжение   следует!",
                "ПРОДОЛЖЕНИЕ СЛЕДУЕТ..."]


def say(stream, text: str) -> None:
    stream.buffer.write(text.encode("utf-8"))
    stream.flush()


def fail(message: str, code: int = 2) -> None:
    say(sys.stderr, f"fake-whisper: {message}\n")
    sys.exit(code)


def parse(argv: list) -> tuple:
    opts, flags = {}, set()
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in VALUE_OPTS:
            if i + 1 >= len(argv):
                fail(f"нет значения для {a}")
            opts[a] = argv[i + 1]
            i += 2
        else:
            flags.add(a)
            i += 1
    return opts, flags


def ts_srt(sec: float) -> str:
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def ts_out(sec: float) -> str:
    return ts_srt(sec).replace(",", ".")


def signal_by_second(path: str) -> tuple:
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        duration = w.getnframes() / float(rate)
        means = []
        while True:
            data = w.readframes(rate)
            if not data:
                break
            samples = array.array("h")
            samples.frombytes(data[: len(data) - len(data) % 2])
            means.append(sum(samples) / len(samples) if len(samples) else 0.0)
    return duration, means


def label(mean: float) -> str:
    if mean > 100:
        return "плюс"
    if mean < -100:
        return "минус"
    return "ноль"


def normal_segments(duration: float, means: list) -> list:
    segs, start, k = [], 0.0, 1
    while start < duration - 1e-6:
        end = min(duration, start + 2.0)
        window = means[int(start): max(int(start) + 1, int(end))]
        mean = sum(window) / len(window) if window else 0.0
        segs.append((start, end, f"Фраза {k}: сигнал {label(mean)}."))
        start, k = end, k + 1
    return segs


def loop_segments() -> list:
    segs = [(float(s), float(s + 2), f"Обычная речь, предложение {s // 2 + 1}.")
            for s in range(0, 20, 2)]
    for n in range(180):
        segs.append((20.0 + n, 21.0 + n, LOOP_PHRASES[n % len(LOOP_PHRASES)]))
    return segs


def read_mode() -> tuple:
    """Режим и длительность: из переменных окружения или из файла
    FAKE_WHISPER_MODE_FILE («slow 20», «loop»…) — его меняет демо-режим."""
    mode = os.environ.get("FAKE_WHISPER_MODE", "normal")
    seconds = os.environ.get("FAKE_WHISPER_SECONDS", "30")
    mode_file = os.environ.get("FAKE_WHISPER_MODE_FILE")
    if mode_file and os.path.isfile(mode_file):
        with open(mode_file, encoding="utf-8") as fh:
            parts = fh.read().split()
        if parts:
            mode = parts[0]
        if len(parts) > 1:
            seconds = parts[1]
    return mode, float(seconds)


def progress(p: int) -> None:
    say(sys.stderr, f"whisper_print_progress_callback: progress = {p:3d}%\n")


def write_outputs(base: str, segs: list) -> None:
    with open(base + ".srt", "w", encoding="utf-8", newline="\n") as fh:
        for n, (s, e, text) in enumerate(segs, 1):
            fh.write(f"{n}\n{ts_srt(s)} --> {ts_srt(e)}\n {text}\n\n")
    with open(base + ".txt", "w", encoding="utf-8", newline="\n") as fh:
        for _s, _e, text in segs:
            fh.write(f" {text}\n")


def main(argv: list) -> None:
    opts, flags = parse(argv)
    if "-nf" in flags:
        fail("получен -nf — его добавлять нельзя")
    if opts.get("-l") != "ru":
        fail(f"язык должен быть -l ru, получено {opts.get('-l')}")
    if opts.get("-mc") != "0":
        fail("нет -mc 0 — без него модель зацикливается")
    for need in ("-otxt", "-osrt"):
        if need not in flags:
            fail(f"нет {need}")
    for need in ("-m", "-f", "-of"):
        if need not in opts:
            fail(f"нет {need}")
    if not os.path.isfile(opts["-m"]):
        fail(f"модель не найдена: {opts['-m']}", 3)
    if not os.path.isfile(opts["-f"]):
        fail(f"входной файл не найден: {opts['-f']}", 3)
    say(sys.stderr, f"whisper_init_from_file_with_params_no_state: loading model from '{opts['-m']}'\n")
    duration, means = signal_by_second(opts["-f"])
    mode, total = read_mode()
    if mode == "error":
        progress(5)
        fail("имитация сбоя распознавания", 3)
    if mode == "slow":
        beat_path = os.environ.get("FAKE_WHISPER_HEARTBEAT")
        start, beat, last = time.time(), 0, -1
        while time.time() - start < total:
            beat += 1
            if beat_path:
                with open(beat_path, "w", encoding="utf-8") as fh:
                    fh.write(str(beat))
            p = int(100 * (time.time() - start) / total) // 5 * 5
            if p > last and "-pp" in flags:
                progress(p)
                last = p
            time.sleep(0.05)
    segs = loop_segments() if mode == "loop" else normal_segments(duration, means)
    for s, e, text in segs:
        say(sys.stdout, f"[{ts_out(s)} --> {ts_out(e)}]   {text}\n")
    if "-pp" in flags:
        progress(100)
    write_outputs(opts["-of"], segs)


if __name__ == "__main__":
    main(sys.argv[1:])
