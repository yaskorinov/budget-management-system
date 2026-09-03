"""Rich Markdown — формат rich-сообщений Telegram (InputRichMessage.markdown).

Совместим с GitHub Flavored Markdown: заголовки, таблицы, списки, цитаты,
сворачиваемые блоки. Отправляется методом sendRichMessage, а не sendMessage,
и parse_mode к нему отношения не имеет.

Как и в MarkdownV2, весь текст извне обязан быть экранирован, иначе имя вида
«**Аня**» или «[Боря]» превратится в разметку. Тип Rich помечает готовые
фрагменты, чтобы их не экранировали повторно.
"""
from __future__ import annotations

import re

BACKSLASH = chr(92)
# Пунктуация, с которой в GFM начинается разметка
SPECIALS = "`*_{}[]()#+-.!|~=<>$^" + BACKSLASH

_SPECIALS_RE = re.compile("([" + re.escape(SPECIALS) + "])")


def _escape_with(match: re.Match) -> str:
    return BACKSLASH + match.group(1)


class Rich(str):
    """Фрагмент готовой разметки — повторно не экранируется."""


def esc(value: object) -> Rich:
    text = str(value if value is not None else "")
    return Rich(_SPECIALS_RE.sub(_escape_with, text))


def raw(value: object) -> Rich:
    """Только для собственных констант."""
    return Rich(str(value))


def join(*parts: object, sep: str = "") -> Rich:
    return Rich(sep.join(p if isinstance(p, Rich) else esc(p) for p in parts))


def lines(*parts: object) -> Rich:
    return join(*parts, sep=chr(10))


def blocks(*parts: object) -> Rich:
    """Абзацы: между блоками пустая строка, иначе они склеятся в один."""
    kept = [p for p in parts if str(p) != ""]
    return join(*kept, sep=chr(10) * 2)


def bold(*parts: object) -> Rich:
    return Rich("**" + join(*parts) + "**")


def italic(*parts: object) -> Rich:
    return Rich("*" + join(*parts) + "*")


def strike(*parts: object) -> Rich:
    return Rich("~~" + join(*parts) + "~~")


def marked(*parts: object) -> Rich:
    """Выделение маркером — своё у rich-разметки."""
    return Rich("==" + join(*parts) + "==")


def spoiler(*parts: object) -> Rich:
    return Rich("||" + join(*parts) + "||")


def code(value: object) -> Rich:
    """Строчный код. Внутри разметка не работает, экранировать нечего."""
    text = str(value).replace("`", "'")
    return Rich("`" + text + "`")


def pre(value: object, language: str = "") -> Rich:
    fence = "```"
    return Rich(fence + language + chr(10) + str(value) + chr(10) + fence)


def link(text: object, url: str) -> Rich:
    return Rich("[" + join(text) + "](" + url.replace(")", "%29") + ")")


def heading(level: int, *parts: object) -> Rich:
    return Rich("#" * max(1, min(level, 6)) + " " + join(*parts))


def bullets(*items: object) -> Rich:
    return lines(*[join(raw("- "), item) for item in items])


def numbered(*items: object) -> Rich:
    return lines(*[join(raw(str(i) + ". "), item) for i, item in enumerate(items, 1)])


def quote(*rows: object) -> Rich:
    """Цитата. Пустая строка внутри — это «>» без текста."""
    body = join(*rows, sep=chr(10))
    return lines(*[raw("> " + row) for row in str(body).split(chr(10))])


def rule() -> Rich:
    return Rich("---")


def details(summary: object, body: object, *, expanded: bool = False) -> Rich:
    """Сворачиваемый блок. Разметка внутри <details> разбирается."""
    tag = "<details open>" if expanded else "<details>"
    return lines(
        join(raw(tag), raw("<summary>"), join(summary), raw("</summary>")),
        "",
        join(body),
        "",
        raw("</details>"),
    )


def table(headers: list[object], rows: list[list[object]], align: str = "") -> Rich:
    """Таблица. В ячейках допустима только строчная разметка, переносов быть не может.

    align — по букве на столбец: l, c, r. Пусто — всё по левому краю.
    """
    marks = {"l": ":---", "c": ":---:", "r": "---:"}
    widths = len(headers)
    align = (align or "l" * widths).ljust(widths, "l")

    def cell(value: object) -> str:
        return str(join(value)).replace(chr(10), " ")

    out = [
        "| " + " | ".join(cell(h) for h in headers) + " |",
        "|" + "|".join(marks.get(a, ":---") for a in align[:widths]) + "|",
    ]
    out += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return Rich(chr(10).join(out))
