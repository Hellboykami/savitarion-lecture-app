#!/usr/bin/env python3
"""Самопроверка приложения «Расшифровка лекций».

    python3 selftest.py                  — все проверки за секунды, без whisper и без сети
    python3 selftest.py --live <запись>  — живая проверка на маке настоящими программами

Пишет только в test-root/ внутри папки проекта.
"""
from __future__ import annotations

import platform
import sys
import time


def main(argv: list) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if argv and argv[0] == "--live":
        from testing import live
        return live.main(" ".join(argv[1:]) if len(argv) > 1 else None)
    if argv:
        print("Запуск: python3 selftest.py   или   python3 selftest.py --live <файл записи>")
        return 2

    from testing import harness
    # Импорт регистрирует проверки — порядок важен: от простого к сложному.
    from testing import checks_compat, checks_units, checks_layout  # noqa: F401
    from testing import checks_jobs, checks_live  # noqa: F401
    from testing import checks_server, checks_launch, checks_pack  # noqa: F401

    env = harness.Env()
    print(f"Python {platform.python_version()} — {sys.executable}")
    print("ffmpeg: " + ("настоящий" if env.real_ffmpeg
                        else "имитация (на этом компьютере ffmpeg нет)") + "; whisper-cli: имитация")
    print("caffeinate: " + ("есть — отмена проверяется вместе с ним" if env.caffeinate
                            else "нет на этом компьютере — пропускаю"))
    print(f"Тестовый корень: {env.root}\n")
    started = time.time()
    env.prepare()
    try:
        failed, skipped = harness.run_checks(env)
    finally:
        env.close()
    total = len(harness.CHECKS)
    took = time.time() - started
    passed = total - failed - skipped
    tail = f", пропущено {skipped} (их можно проверить только на маке)" if skipped else ""
    if failed:
        print(f"\nИтог: не прошло {failed} из {total}{tail} — подробности у строк «ОШИБКА» "
              f"выше ({took:.1f} с).")
    else:
        print(f"\nИтог: пройдено {passed} из {total}{tail}; за {took:.1f} с.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
