"""MarkdownV2 по правилам Telegram.

Разметка собирается функциями, а не руками в f-строках: любой текст извне —
имена, названия бюджетов, описания покупок — обязан быть экранирован, иначе
Telegram отвергает сообщение целиком. Готовые фрагменты помечены типом Markup
и повторно не экранируются.

Правила (core.telegram.org/bots/api#markdownv2-style):
  * в обычном тексте экранируются _*[]()~`>#+-=|{}.! и сам обратный слэш;
  * внутри code и pre — только ` и обратный слэш;
  * цитата — префикс > у каждой строки; сворачиваемая — ещё ** в начале и || в конце.
"""
from __future__ import annotations

import re

BACKSLASH = chr(92)
TEXT_SPECIALS = "_*[]()~`>#+-=|{}.!" + BACKSLASH
CODE_SPECIALS = "`" + BACKSLASH

_TEXT_RE = re.compile("([" + re.escape(TEXT_SPECIALS) + "])")
_CODE_RE = re.compile("([" + re.escape(CODE_SPECIALS) + "])")
_LINK_RE = re.compile("([" + re.escape(")" + BACKSLASH) + "])")

def _escape_with(match: re.Match) -> str:
    """Функция вместо шаблона: в шаблоне обратные слэши считать слишком легко."""
    return BACKSLASH + match.group(1)


class Markup(str):
    """Фрагмент, уже пригодный для отправки."""


def esc(value: object) -> Markup:
    """Экранирует обычный текст."""
    return Markup(_TEXT_RE.sub(_escape_with, str(value if value is not None else "")))


def raw(value: object) -> Markup:
    """Помечает строку как готовую разметку. Только для собственных констант."""
    return Markup(str(value))


def join(*parts: object, sep: str = "") -> Markup:
    """Склеивает части: Markup — как есть, остальное экранируется."""
    return Markup(
        sep.join(part if isinstance(part, Markup) else esc(part) for part in parts)
    )


def lines(*parts: object) -> Markup:
    return join(*parts, sep=chr(10))


def bold(*parts: object) -> Markup:
    return Markup("*" + join(*parts) + "*")


def italic(*parts: object) -> Markup:
    return Markup("_" + join(*parts) + "_")


def underline(*parts: object) -> Markup:
    return Markup("__" + join(*parts) + "__")


def strike(*parts: object) -> Markup:
    return Markup("~" + join(*parts) + "~")


def spoiler(*parts: object) -> Markup:
    return Markup("||" + join(*parts) + "||")


def code(value: object) -> Markup:
    """Моноширинный фрагмент внутри строки."""
    return Markup("`" + _CODE_RE.sub(_escape_with, str(value)) + "`")


def pre(value: object, language: str = "") -> Markup:
    """Моноширинный блок — единственное место, где колонки встают ровно."""
    body = _CODE_RE.sub(_escape_with, str(value))
    return Markup("```" + language + chr(10) + body + chr(10) + "```")


def link(text: object, url: str) -> Markup:
    return Markup("[" + join(text) + "](" + _LINK_RE.sub(_escape_with, url) + ")")


def quote(body: object, *, expandable: bool = False) -> Markup:
    """Цитата. Сворачиваемая прячет всё, что не влезло в первые строки."""
    rows = [">" + row for row in str(body).split(chr(10))]
    if expandable:
        # ** — пустая жирная сущность: ею Telegram помечает сворачиваемую цитату
        rows[0] = "**" + rows[0]
        rows[-1] = rows[-1] + "||"
    return Markup(chr(10).join(rows))
