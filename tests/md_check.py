"""Разбор MarkdownV2 так же, как это делает Telegram.

Общий для тестов: любое сообщение бота обязано пройти эту проверку, иначе
Telegram откажется его отправлять с ошибкой разбора сущностей.
"""
from __future__ import annotations

from app.bot.markup import BACKSLASH

# Парные маркеры, которые мы используем
TOGGLES = {"*": "bold", "__": "underline", "_": "italic", "~": "strike", "||": "spoiler"}
# Эти символы разметкой у нас не бывают никогда — только экранированными
ALWAYS_ESCAPED = set("[]()#+-={}.!")


def validate(markup: str) -> list[str]:
    """Разбирает MarkdownV2 так же, как это сделает Telegram."""
    errors: list[str] = []
    stack: list[str] = []
    i, line_start = 0, True
    expandable = False  # открыта сворачиваемая цитата: её закроет || в конце строки
    n = len(markup)

    while i < n:
        ch = markup[i]

        if ch == BACKSLASH:  # экранированный символ — пропускаем пару
            i += 2
            line_start = False
            continue

        if markup.startswith("```", i):  # блок кода: до закрывающих тройных кавычек
            end = markup.find("```", i + 3)
            if end == -1:
                errors.append("не закрыт блок ```")
                break
            i = end + 3
            line_start = False
            continue

        if ch == "`":  # строчный код
            end = i + 1
            while end < n and markup[end] != "`":
                end += 2 if markup[end] == BACKSLASH else 1
            if end >= n:
                errors.append("не закрыт `code`")
                break
            i = end + 1
            line_start = False
            continue

        if ch == chr(10):
            i += 1
            line_start = True
            continue

        if line_start and markup.startswith("**>", i):  # сворачиваемая цитата
            expandable = True
            i += 3
            line_start = False
            continue

        # || в конце строки сворачиваемой цитаты — маркер, а не спойлер
        if expandable and markup.startswith("||", i) and (i + 2 == n or markup[i + 2] == chr(10)):
            expandable = False
            i += 2
            continue

        if line_start and ch == ">":  # обычная цитата
            i += 1
            line_start = False
            continue

        matched = next((m for m in ("__", "||", "*", "_", "~") if markup.startswith(m, i)), None)
        if matched:
            name = TOGGLES[matched]
            if stack and stack[-1] == name:
                stack.pop()
            else:
                stack.append(name)
            i += len(matched)
            line_start = False
            continue

        if ch in ALWAYS_ESCAPED or ch in ">|":
            errors.append(f"неэкранированный {ch!r} в позиции {i}")

        i += 1
        line_start = False

    if stack:
        errors.append(f"не закрыты сущности: {stack}")
    if expandable:
        errors.append("сворачиваемая цитата без закрывающего ||")
    return errors


