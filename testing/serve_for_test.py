"""Приложение в отдельном процессе для проверки сигнала SIGHUP (закрытие окна
Терминала): python serve_for_test.py <папка> <порт> <домашняя папка>.
Распознавание — имитация; всё пишется в указанную папку внутри test-root."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from lecapp import config, launcher  # noqa: E402
from testing.harness import fake_cmd  # noqa: E402


def main(argv: list) -> int:
    root, port, home = Path(argv[0]), int(argv[1]), Path(argv[2])
    paths = config.Paths(project=root, tmp=root / "tmp", logs=root / "logs",
                         settings_file=root / "settings.json", home=home)
    caffeinate = config.find_program("caffeinate")
    tools = config.Tools(ffmpeg=fake_cmd("ffmpeg"), ffprobe=fake_cmd("ffprobe"),
                         whisper=fake_cmd("whisper"),
                         caffeinate=[caffeinate] if caffeinate else None,
                         osascript=fake_cmd("osascript"), open=None,
                         model=home / "whisper-models" / "ggml-large-v3-turbo.bin")
    return launcher.run(paths, tools, mode="test", port=port, open_browser=False)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
