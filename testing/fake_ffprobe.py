"""Имитация ffprobe для selftest:
    -v error -print_format json -show_format -show_streams ФАЙЛ
Отдаёт длительность WAV и теги из блока fktg (их пишет fake_ffmpeg).
"""
from __future__ import annotations

import json
import sys
import wave

import fakewav


def main(argv: list) -> None:
    if not argv or "json" not in argv:
        sys.stderr.buffer.write("fake-ffprobe: ожидался -print_format json\n".encode("utf-8"))
        sys.exit(2)
    path = argv[-1]
    try:
        with wave.open(path, "rb") as w:
            rate, frames, channels = w.getframerate(), w.getnframes(), w.getnchannels()
    except Exception:  # noqa: BLE001 — любой нечитаемый файл
        sys.stderr.buffer.write(f"{path}: Invalid data found when processing input\n".encode("utf-8"))
        sys.exit(1)
    duration = f"{frames / float(rate):.6f}"
    info = {
        "streams": [{"index": 0, "codec_name": "pcm_s16le", "codec_type": "audio",
                     "sample_rate": str(rate), "channels": channels, "duration": duration}],
        "format": {"filename": path, "format_name": "wav", "duration": duration,
                   "tags": fakewav.read_tags(path)},
    }
    sys.stdout.buffer.write(json.dumps(info, ensure_ascii=False, indent=4).encode("utf-8"))


if __name__ == "__main__":
    main(sys.argv[1:])
