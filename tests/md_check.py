"""Разбор Rich Markdown — того, что уходит в InputRichMessage.markdown.

Формат совместим с GitHub Flavored Markdown, поэтому проверяем не парность
каждого символа, а то, ради чего экранирование и нужно: чтобы данные извне
(имена, названия, описания) не превращались в разметку, и чтобы структура —
таблицы, сворачиваемые блоки, блоки кода — оставалась целой.
"""
from __future__ import annotations

import re

from app.bot.rich import BACKSLASH

BLOCK_TAGS = ("details", "tg-collage", "tg-slideshow")


def _strip_escapes(text: str) -> str:
    """Убирает экранированные пары, чтобы они не мешали разбору."""
    return re.sub(re.escape(BACKSLASH) + ".", "", text, flags=re.S)


def validate(markup: str) -> list[str]:
    errors: list[str] = []
    text = str(markup)

    # Блоки кода вырезаем: внутри разметки нет
    if text.count("```") % 2:
        errors.append("непарные ограждения ```")
    text = re.sub(r"```.*?```", "", text, flags=re.S)

    bare = _strip_escapes(text)

    for tag in BLOCK_TAGS:
        opened = len(re.findall(r"<" + tag + r"[ >]", bare))
        closed = bare.count("</" + tag + ">")
        if opened != closed:
            errors.append(f"<{tag}>: открыто {opened}, закрыто {closed}")

    # Таблица: у всех строк одинаковое число столбцов, есть строка выравнивания
    for block in re.split(r"(?:" + chr(10) + r"){2,}", bare):
        rows = [r for r in block.split(chr(10)) if r.startswith("|")]
        if not rows:
            continue
        if len(rows) < 2 or not re.fullmatch(r"\|(?::?-+:?\|)+", rows[1]):
            errors.append("таблица без строки выравнивания: " + rows[0][:40])
            continue
        widths = {row.count("|") for row in rows}
        if len(widths) != 1:
            errors.append(f"в таблице строки разной ширины: {sorted(widths)}")

    # Внутри ячеек переносов быть не может
    for row in bare.split(chr(10)):
        if row.startswith("|") and row.endswith("|") and row.count("|") < 2:
            errors.append("сломанная строка таблицы: " + row[:40])

    return errors


def find_unescaped(markup: str, sample: str) -> list[str]:
    """Данные извне не должны попадать в сообщение как есть.

    Проверяем не отдельные символы (они законно встречаются в нашей же
    разметке), а куски подставленного текста: если фрагмент виден дословно,
    значит его не экранировали и он станет разметкой.
    """
    text = str(markup)
    problems = []
    if sample and sample in text:
        problems.append("подставленный текст попал в сообщение неэкранированным")
    for token in sample.split():
        # Одиночные знаки бессмысленны: они законно встречаются и в нашей разметке
        if len(token) > 2 and any(ch in token for ch in "*_[]`") and token in text:
            problems.append(f"фрагмент {token!r} не экранирован")
    return problems
