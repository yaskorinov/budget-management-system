"""Ежедневные сообщения: совет по расходам и напоминание о задолженностях.

Отдельного планировщика не заводим: это одна фоновая задача, которая просыпается
раз в минуту и смотрит, не наступил ли нужный час. Отметка о выполнении лежит
в базе, поэтому перезапуск в тот же час не рассылает всё повторно.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InputRichMessage

from app.bot import texts
from app.config import settings
from app.core import insights, periods, service
from app.db.base import session_scope
from app.db.models import Group

log = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 60


async def _send(bot: Bot, chat_id: int, markdown: object) -> bool:
    try:
        await bot.send_rich_message(
            chat_id=chat_id,
            rich_message=InputRichMessage(markdown=str(markdown)),
            disable_notification=True,  # это не срочное сообщение
        )
        return True
    except TelegramAPIError as exc:
        log.warning("Не удалось написать в чат %s (%s)", chat_id, exc)
        return False


async def send_tip(bot: Bot, group: Group) -> bool:
    """Совет по оптимизации расходов."""
    async with session_scope() as session:
        group = await session.get(Group, group.id)
        tip = await insights.spending_tip(session, group)
        if tip is None:
            return False
        text = texts.blocks(
            texts.heading(2, "💡 Совет по расходам"),
            texts.join(tip),
            texts.italic("Считает ИИ по вашим тратам за месяц"),
        )
    return await _send(bot, group.tg_chat_id, text)


async def send_debts(bot: Bot, group: Group) -> bool:
    """Напоминание тем, у кого баланс в минусе."""
    async with session_scope() as session:
        group = await session.get(Group, group.id)
        data = await service.summary(session, group=group)
        debtors = [item for item in data.members if item.balance < 0]
        if not debtors:
            return False

        note = await insights.debt_note()
        text = texts.blocks(
            texts.heading(2, "🔔 Общий фонд"),
            texts.join(note),
            texts.table(
                ["Участник", "Нужно внести"],
                [
                    [item.user.short_name, texts.bold(texts.money(-item.balance))]
                    for item in debtors
                ],
                align="lr",
            ),
            texts.italic("Пополнить: /add 5000"),
        )
    return await _send(bot, group.tg_chat_id, text)


JOBS = {"tips": (lambda: settings.tips_hour, send_tip),
        "debts": (lambda: settings.debts_hour, send_debts)}


async def run_due_jobs(bot: Bot, now: dt.datetime | None = None) -> int:
    """Выполняет задания, чей час уже наступил сегодня. Возвращает число отправок."""
    now = now or dt.datetime.now(periods.TZ).replace(tzinfo=None)
    sent = 0

    async with session_scope() as session:
        groups = await service.groups_with_chats(session)

    for name, (hour_of, handler) in JOBS.items():
        hour = hour_of()
        if hour < 0 or now.hour < hour:
            continue
        for group in groups:
            async with session_scope() as session:
                claimed = await service.claim_daily_job(
                    session, group_id=group.id, job=name, today=now.date()
                )
            if not claimed:
                continue
            try:
                if await handler(bot, group):
                    sent += 1
            except Exception as exc:  # рассылка не должна ронять фоновую задачу
                log.exception("Задание %s для группы %s не выполнено: %s",
                              name, group.id, exc)
    return sent


async def daily_loop(bot: Bot) -> None:
    """Фоновая задача: раз в минуту проверяет, не пора ли писать."""
    log.info(
        "Ежедневные сообщения: совет в %s, напоминание в %s (час, -1 — выключено)",
        settings.tips_hour, settings.debts_hour,
    )
    while True:
        try:
            await run_due_jobs(bot)
        except Exception as exc:  # pragma: no cover — сеть, база
            log.exception("Ежедневные сообщения: сбой (%s)", exc)
        await asyncio.sleep(CHECK_EVERY_SECONDS)
