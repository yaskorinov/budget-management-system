"""Общие помощники хендлеров."""
from __future__ import annotations

from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import service
from app.db.models import Group, User

GROUP_CHATS = {"group", "supergroup"}


def web_app_url(user_token: str | None = None) -> str | None:
    if not settings.web_enabled:
        return None
    return f"{settings.public_base}/?src=tg"


async def resolve_group(
    session: AsyncSession, message: Message, user: User, *, join: bool = False
) -> Group | None:
    """Группа для сообщения: чат — свой бюджет, личка — активная группа."""
    if message.chat.type in GROUP_CHATS:
        title = message.chat.title or "Общий бюджет"
        group = await service.get_or_create_group_for_chat(
            session, tg_chat_id=message.chat.id, title=title
        )
        if join or await service.is_member(session, group_id=group.id, user_id=user.id):
            await service.ensure_member(session, group_id=group.id, user_id=user.id)
            user.active_group_id = group.id
            await session.flush()
        return group
    return await service.resolve_active_group(session, user)


NO_GROUP_HINT = (
    "У вас пока нет общего бюджета.\n\n"
    "• Добавьте бота в общий чат и отправьте там /join — бюджет создастся сам.\n"
    "• Или создайте бюджет прямо здесь: /newgroup Название"
)


def is_private(callback: CallbackQuery) -> bool:
    """Личный чат с ботом? Сообщения из inline-режима приватными не считаются."""
    return bool(callback.message and callback.message.chat.type == "private")


async def edit_card(
    bot: Bot,
    callback: CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Показывает текст на месте нажатой кнопки.

    Сообщение с картинкой (диаграмма статистики) текстом не правится — Telegram
    отвечает «there is no text in the message to edit», поэтому такое сообщение
    заменяем новым.
    """
    try:
        if callback.inline_message_id:
            await bot.edit_message_text(
                text=text,
                inline_message_id=callback.inline_message_id,
                reply_markup=markup,
            )
            return

        message = callback.message
        if message is None:
            return

        if getattr(message, "text", None) is None:
            with suppress(TelegramBadRequest):
                await message.delete()
            await bot.send_message(message.chat.id, text, reply_markup=markup)
            return

        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


async def group_for_callback(session: AsyncSession, callback: CallbackQuery, user: User):
    """Бюджет, к которому относится нажатая кнопка.

    В групповом чате это всегда бюджет самого чата: у нажавшего активной может
    быть совсем другая группа, и статистика показала бы чужие цифры.
    """
    chat = getattr(callback.message, "chat", None)
    if chat is not None and chat.type in GROUP_CHATS:
        return await service.get_or_create_group_for_chat(
            session, tg_chat_id=chat.id, title=chat.title or "Общий бюджет"
        )
    return await service.resolve_active_group(session, user)


async def drop_prompt(bot: Bot, data: dict) -> None:
    """Убирает приглашение «напишите сумму», когда ответ получен."""
    chat_id, message_id = data.get("prompt_chat_id"), data.get("prompt_id")
    if chat_id and message_id:
        with suppress(TelegramBadRequest):
            await bot.delete_message(chat_id, message_id)


async def show_operation_card(
    bot: Bot,
    callback: CallbackQuery,
    session: AsyncSession,
    operation,
    *,
    header: str | None = None,
) -> None:
    from app.bot import keyboards, texts

    group = await session.get(Group, operation.group_id)
    members = await service.group_members(session, operation.group_id)
    await edit_card(
        bot,
        callback,
        texts.operation_card(
            operation, group=group, header=header, members_total=len(members)
        ),
        keyboards.operation_kb(operation, private=is_private(callback)),
    )
