"""Ежедневные подсказки: совет по расходам и напоминание о долгах.

Цифры считаем сами и передаём модели готовыми — просить её считать по сырым
операциям значит ловить ошибки в арифметике. Модель отвечает только за
формулировку; если она недоступна, остаётся запасной текст.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import categories as cat
from app.core import periods, service
from app.core.classifier import chat
from app.core.money import format_money
from app.db.models import Group

log = logging.getLogger(__name__)

TIP_SYSTEM = (
    "Ты помогаешь людям, которые ведут общий бюджет: соседи по квартире, семья, "
    "друзья. По сводке расходов дай ОДИН короткий практичный совет, как тратить "
    "меньше без потери качества жизни.\n"
    "Правила:\n"
    "- две-три строки, живым русским языком, без канцелярита;\n"
    "- опирайся на конкретную статью расходов из сводки и называй суммы из неё;\n"
    "- никаких выдуманных цифр, только те, что даны;\n"
    "- без вступлений вроде «конечно» и без списков — просто совет."
)

DEBT_SYSTEM = (
    "Ты ведёшь общий бюджет компании друзей. Напиши одну короткую дружелюбную "
    "фразу-напоминание тем, у кого баланс ушёл в минус: пора пополнить общий фонд.\n"
    "Правила:\n"
    "- одна строка, тёплый тон, без упрёков и без морали;\n"
    "- не перечисляй имена и суммы, они будут показаны отдельно;\n"
    "- без приветствий и без подписи."
)

DEBT_SPLIT_SYSTEM = (
    "Ты ведёшь общий бюджет компании друзей, где расходы делят между собой. "
    "Напиши одну короткую дружелюбную фразу-напоминание тем, кто остался "
    "должен друзьям.\n"
    "Правила:\n"
    "- одна строка, тёплый тон, без упрёков и без морали;\n"
    "- не перечисляй имена и суммы, они будут показаны отдельно;\n"
    "- без приветствий и без подписи."
)

DEBT_FALLBACK = "Общий фонд просит пополнения — как будет удобно."
DEBT_SPLIT_FALLBACK = "Долги сами себя не вернут — как будет удобно."


async def spending_tip(session: AsyncSession, group: Group) -> str | None:
    """Совет по расходам за месяц. None — если тратить пока не на чем."""
    since, until, period_title = periods.bounds("month")
    rows = await service.stats_by_category(
        session, group_id=group.id, since=since, until=until
    )
    total = sum(value for _, value in rows)
    if total <= 0 or not settings.llm_enabled:
        return None

    summary = "\n".join(
        f"- {cat.get(code).title}: {format_money(value)} ({value / total * 100:.0f}%)"
        for code, value in rows
    )
    operations = await service.list_operations(
        session, group_id=group.id, kind="purchase", limit=15, since=since, until=until
    )
    recent = "\n".join(
        f"- {op.title or cat.get(op.category).title}: {format_money(op.amount)}"
        for op in operations
    )

    prompt = (
        f"Бюджет: {group.title}\n"
        f"Период: {period_title}\n"
        f"Всего потрачено: {format_money(total)}\n\n"
        f"По статьям:\n{summary}\n\n"
        f"Последние покупки:\n{recent}"
    )

    try:
        answer = await chat(
            [
                {"role": "system", "content": TIP_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=300,
            temperature=0.7,  # один и тот же совет каждый день быстро надоест
            timeout=30.0,
        )
    except Exception as exc:
        log.warning("Совет по расходам не получен (%s)", exc)
        return None

    return (answer or "").strip() or None


async def debt_note(*, split: bool = False) -> str:
    """Дружелюбная строка перед списком должников."""
    system = DEBT_SPLIT_SYSTEM if split else DEBT_SYSTEM
    fallback = DEBT_SPLIT_FALLBACK if split else DEBT_FALLBACK
    ask = "Напомни про возврат долгов." if split else "Напомни про пополнение фонда."

    if not settings.llm_enabled:
        return fallback
    try:
        answer = await chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": ask},
            ],
            max_tokens=80,
            temperature=0.9,
            timeout=20.0,
        )
    except Exception as exc:
        log.warning("Напоминание не сформулировано (%s)", exc)
        return fallback

    return (answer or "").strip().splitlines()[0] if answer else fallback
