"""Совместимость с Python 3.9 и чистота файлов проекта.

На ноутбуке Python новее, чем может быть на маке, и простой запуск здесь не
поймает приёмы из новых версий. Эта проверка читает каждый .py так, как его
прочитал бы Python 3.9, и ищет запрещённое в CLAUDE.md. Полной гарантии нет
(например, вложенные одинаковые кавычки в f-строках она не видит) —
окончательно покажет python3 selftest.py на маке.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from .harness import PROJECT, check, expect

SKIP_DIRS = {"test-root", "tmp", "logs", "__pycache__", ".git"}
TEXT_EXT = {".py", ".command", ".html", ".css", ".js", ".json", ".md", ".txt"}
INVISIBLE = {0xFEFF, 0x200B, 0x200C, 0x200D, 0x2060}

NAMES = {
    "pairwise": "itertools.pairwise — с 3.10",
    "batched": "itertools.batched — с 3.12",
    "tomllib": "tomllib — с 3.11",
    "fromisoformat": "fromisoformat в 3.9 не понимает «Z» — даты разбираем вручную",
    "StrEnum": "enum.StrEnum — с 3.11",
    "TaskGroup": "asyncio.TaskGroup — с 3.11",
    "ExceptionGroup": "ExceptionGroup — с 3.11",
}
ATTRS = {
    ("datetime", "UTC"): "datetime.UTC — с 3.11, нужен timezone.utc",
    ("contextlib", "chdir"): "contextlib.chdir — с 3.11",
    ("typing", "Self"): "typing.Self — с 3.11",
    ("typing", "override"): "typing.override — с 3.12",
    ("typing", "TypeAlias"): "typing.TypeAlias — с 3.10",
}
KEYWORDS_ANYWHERE = {
    "process_group": "subprocess(process_group=) — с 3.11, нужен start_new_session=True",
    "onexc": "shutil.rmtree(onexc=) — с 3.12",
}
KEYWORDS_IN_CALL = {
    ("zip", "strict"): "zip(strict=) — с 3.10",
    ("dataclass", "slots"): "dataclass(slots=) — с 3.10",
    ("dataclass", "kw_only"): "dataclass(kw_only=) — с 3.10",
    ("write_text", "newline"): "Path.write_text(newline=) — с 3.10",
    ("read_text", "newline"): "Path.read_text(newline=) — с 3.13",
}
TYPE_NAMES = {"int", "str", "float", "bool", "bytes", "list", "dict", "tuple", "set",
              "frozenset", "type", "object", "Path"}
_PAREN_WITH = re.compile(r"^\s*(async\s+)?with\s*\(")


def project_files() -> list:
    result = []
    for path in sorted(PROJECT.rglob("*")):
        rel = path.relative_to(PROJECT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.is_file() and not path.name.endswith(".zip"):
            result.append(path)
    return result


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


class _Finder(ast.NodeVisitor):
    """Ищет приёмы новых версий. Аннотации пропускает: с
    from __future__ import annotations они не вычисляются."""

    def __init__(self) -> None:
        self.found: list = []

    def add(self, node: ast.AST, text: str) -> None:
        self.found.append(f"строка {getattr(node, 'lineno', '?')}: {text}")

    def visit_arg(self, node: ast.arg) -> None:
        return  # аннотация параметра

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for item in node.decorator_list + list(node.args.defaults) + [
                d for d in node.args.kw_defaults if d is not None]:
            self.visit(item)
        for item in node.body:
            self.visit(item)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in NAMES:
            self.add(node, NAMES[node.id])

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in NAMES:
            self.add(node, NAMES[node.attr])
        if isinstance(node.value, ast.Name) and (node.value.id, node.attr) in ATTRS:
            self.add(node, ATTRS[(node.value.id, node.attr)])
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in NAMES:
                self.add(node, NAMES[alias.name])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in NAMES:
                self.add(node, NAMES[alias.name])
            if (node.module, alias.name) in ATTRS:
                self.add(node, ATTRS[(node.module, alias.name)])

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        for kw in node.keywords:
            if kw.arg in KEYWORDS_ANYWHERE:
                self.add(node, KEYWORDS_ANYWHERE[kw.arg])
            if (name, kw.arg) in KEYWORDS_IN_CALL:
                self.add(node, KEYWORDS_IN_CALL[(name, kw.arg)])
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.BitOr):
            for side in (node.left, node.right):
                is_none = isinstance(side, ast.Constant) and side.value is None
                is_type = isinstance(side, ast.Name) and side.id in TYPE_NAMES
                if is_none or is_type:
                    self.add(node, "X | Y вне аннотаций — с 3.10, нужен Optional/Union")
                    break
        self.generic_visit(node)


def py39_problems(path: Path) -> list:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path), feature_version=(3, 9))
    except SyntaxError as err:
        return [f"строка {err.lineno}: Python 3.9 не прочитает: {err.msg}"]
    problems = []
    if "from __future__ import annotations" not in source:
        problems.append("нет from __future__ import annotations")
    for n, line in enumerate(source.splitlines(), 1):
        if _PAREN_WITH.match(line):
            problems.append(f"строка {n}: with (…) в скобках — с 3.10")
    finder = _Finder()
    finder.visit(tree)
    return problems + finder.found


def hygiene_problems(path: Path) -> list:
    problems = []
    rel = path.relative_to(PROJECT)
    if not all(part.isascii() for part in rel.parts):
        problems.append("имя не латиницей — архив с Windows может его исказить")
    if path.suffix.lower() not in TEXT_EXT:
        return problems
    data = path.read_bytes()
    if data.startswith(bytes([0xEF, 0xBB, 0xBF])):
        problems.append("в начале BOM")
    if b"\r" in data:
        problems.append("перевод строки CRLF, нужен LF")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return problems + ["не UTF-8"]
    odd = sorted({f"U+{ord(ch):04X}" for ch in text
                  if ord(ch) in INVISIBLE or (ord(ch) < 32 and ch not in "\n\t")})
    if odd:
        problems.append("невидимые или управляющие символы: " + ", ".join(odd))
    return problems


@check("код читается Python 3.9 и не использует запрещённое из CLAUDE.md")
def check_py39(env) -> None:
    report = []
    count = 0
    for path in project_files():
        if path.suffix == ".py":
            count += 1
            for problem in py39_problems(path):
                report.append(f"{path.relative_to(PROJECT)}: {problem}")
    expect(count >= 10, f"найдено слишком мало .py файлов: {count}")
    expect(not report, "\n".join(report))


@check("файлы проекта: UTF-8 без BOM, LF, латинские имена, без невидимых символов")
def check_hygiene(env) -> None:
    report = []
    for path in project_files():
        for problem in hygiene_problems(path):
            report.append(f"{path.relative_to(PROJECT)}: {problem}")
    expect(not report, "\n".join(report))


@check("сама проверка 3.9 ловит запрещённое (на примерах)")
def check_py39_self(env) -> None:
    samples = {
        "match x:\n    case 1:\n        pass\n": ("не прочитает",),
        "import itertools\nitertools.pairwise([1, 2])\n": ("pairwise",),
        "zip([1], [2], strict=True)\n": ("strict",),
        "isinstance(1, int | None)\n": ("X | Y",),
        "def f(a: int | None) -> str | None:\n    return None\n": (),
        "import datetime\ndatetime.UTC\n": ("UTC",),
        "with (open('a') as a, open('b') as b):\n    pass\n": ("в скобках", "не прочитает"),
    }
    tmp = env.root / "py39-samples"
    tmp.mkdir(parents=True, exist_ok=True)
    for n, (code, markers) in enumerate(samples.items(), 1):
        sample = tmp / f"sample{n}.py"
        sample.write_text("from __future__ import annotations\n" + code, encoding="utf-8")
        problems = " ".join(py39_problems(sample))
        if markers:
            expect(any(m in problems for m in markers),
                   f"пример {n} не пойман: {code!r} -> {problems!r}")
        else:
            expect(not problems, f"ложная тревога на примере {n}: {problems}")
