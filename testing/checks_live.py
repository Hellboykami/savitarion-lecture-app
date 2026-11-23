"""Репетиция живой проверки на имитациях: код --live проходит целиком и
здесь, хотя настоящая живая проверка возможна только на маке."""
from __future__ import annotations

import contextlib
import io

from . import live
from .harness import check, expect, fake_cmd, fake_mode, write_wav


@check("репетиция --live на имитациях: три прогона и проверка старых расшифровок")
def check_live_rehearsal(env) -> None:
    record = write_wav(env.inputs / "long-lecture.m4a", 10.0)
    old = env.home / "whisper-out"
    old.mkdir(parents=True, exist_ok=True)
    blocks = [f"{n + 1}\n00:00:{n:02d},000 --> 00:00:{n + 1:02d},000\n Спасибо за внимание.\n"
              for n in range(8)]
    (old / "старая с петлёй.srt").write_text("\n".join(blocks), encoding="utf-8")
    (old / "обычная.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\n Добрый день.\n",
                                     encoding="utf-8")
    tools = env.tools(ffmpeg=fake_cmd("ffmpeg"), ffprobe=fake_cmd("ffprobe"))
    out = io.StringIO()
    with fake_mode("normal"), contextlib.redirect_stdout(out):
        code = live.run_live(record, tools, env.home, env.root / "live-rehearsal", clip=4.0)
    text = out.getvalue()
    expect(code == 0, "репетиция не прошла:\n" + text[-1500:])
    expect(text.count("готово за") == 3, "не три прогона:\n" + text[-1500:])
    expect("старая с петлёй.srt: сработала" in text and "обычная.srt: петель нет" in text,
           text[-800:])
    expect("сработала в 1" in text, text[-400:])
