"""Свободный текст в личке: пытаемся понять как покупку."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards, texts
from app.bot.common import NO_GROUP_HINT, answer_rich
from app.bot.handlers.entry import record_purchase
from app.core import service
from app.core.money import parse_amount
from app.db.models import User

router = Router(name="fallback")


@router.message(F.chat.type == "private", F.text & ~F.text.startswith("/"))
async def private_text(
    message: Message, session: AsyncSession, user: User, bot: Bot
) -> None:
    amount, _ = parse_amount(message.text)
    if amount is None:
        active = await service.resolve_active_group(session, user)
        second = (
            texts.join("или «отдал 500» — вернуть долг участнику.")
            if active and active.is_split
            else texts.join("или «внёс 5000» для пополнения фонда.")
        )
        await answer_rich(message, 
            texts.lines(
                texts.join(
                    "Не понял. Напишите покупку с суммой — ",
                    texts.code("молоко хлеб 850"),
                ),
                second,
                texts.italic("Меню: /start · Все команды: /help"),
            ),
            reply_markup=keyboards.back_home_kb(),
        )
        return

    group = await service.resolve_active_group(session, user)
    if group is None:
        await answer_rich(message, NO_GROUP_HINT)
        return
    await record_purchase(message, session, user, group, message.text, "dm", bot)
