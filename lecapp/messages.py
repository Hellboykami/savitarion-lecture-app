"""Сообщения пользователю: по-русски и с подсказкой, что делать."""
from __future__ import annotations


class UserError(Exception):
    """Ошибка, которую показываем пользователю как есть."""

    def __init__(self, text: str, hint: str = "") -> None:
        super().__init__(text)
        self.text = text
        self.hint = hint

    def full(self) -> str:
        return self.text + (" " + self.hint if self.hint else "")


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(5, 'раз', 'раза', 'раз') -> 'раз'."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def no_ffmpeg() -> UserError:
    return UserError(
        "Не найдена программа ffmpeg — без неё запись не перевести в нужный формат.",
        "Установите её в Терминале командой brew install ffmpeg и запустите приложение заново.",
    )


def no_whisper() -> UserError:
    return UserError(
        "Не найдена программа whisper-cli, которая распознаёт речь.",
        "Установите её в Терминале командой brew install whisper-cpp и запустите приложение заново.",
    )


def no_model(path: object) -> UserError:
    return UserError(
        f"Не найдена модель распознавания: {path}.",
        "Положите файл ggml-large-v3-turbo.bin в папку whisper-models в домашней папке.",
    )


def unreadable(name: str) -> UserError:
    return UserError(
        f"Файл «{name}» не читается: ffmpeg не смог достать из него звук.",
        "Проверьте, что запись открывается и проигрывается на маке. "
        "Если это не запись — поменяйте вид файла в карточке.",
    )


def whisper_failed(code: int, log_name: str) -> UserError:
    return UserError(
        f"Распознавание остановилось с ошибкой (код {code}).",
        "Попробуйте запустить ещё раз. Если повторится — принесите в Claude Code "
        f"файл logs/{log_name}.",
    )


def whisper_no_result(log_name: str) -> UserError:
    return UserError(
        "Распознавание закончилось, но расшифровка не появилась.",
        f"Попробуйте ещё раз. Если повторится — принесите в Claude Code файл logs/{log_name}.",
    )


def bad_subject(name: str) -> UserError:
    return UserError(
        f"Название предмета «{name}» не подходит для имени папки.",
        "Уберите из него знаки /, \\ и : и не начинайте его с точки.",
    )


def bad_settings(file_name: str, detail: object) -> UserError:
    return UserError(
        f"Файл настроек {file_name} не читается: {detail}.",
        "Исправьте его в текстовом редакторе или удалите — тогда приложение "
        "создаст новый с настройками по умолчанию.",
    )


def bad_date(value: str) -> UserError:
    return UserError(
        f"Дата «{value}» записана неверно.",
        "Нужен вид ГГГГ-ММ-ДД, например 2026-09-07.",
    )


def no_files() -> UserError:
    return UserError("В карточке нет файлов.", "Перетащите в окно материалы лекции.")


def place_failed(err: object, log_name: str) -> UserError:
    return UserError(
        f"Не удалось разложить файлы по папке лекции: {err}.",
        "Проверьте, что на диске есть место. Подробности — в файле "
        f"logs/{log_name}.",
    )


def unexpected(err: object, log_name: str) -> UserError:
    return UserError(
        f"Непредвиденная ошибка: {err}.",
        f"Принесите в Claude Code файл logs/{log_name} — по нему разберёмся.",
    )


def loop_warning(timecode: str, count: int, file_name: str) -> str:
    times = plural(count, "раз", "раза", "раз")
    return (
        f"Возможна петля в расшифровке «{file_name}»: с {timecode} одна фраза "
        f"повторяется {count} {times} подряд. Проверьте это место — текст после "
        "него может быть испорчен."
    )
