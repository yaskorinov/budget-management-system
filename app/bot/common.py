"""Общие помощники хендлеров."""
from __future__ import annotations

import logging
from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
    ReplyParameters,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.config import BASE_DIR, settings
from app.core import service
from app.db.models import Group, User

log = logging.getLogger(__name__)

GROUP_CHATS = {"group", "supergroup"}


def web_app_url(user_token: str | None = None) -> str | None:
    if not settings.web_enabled:
        return None
    return f"{settings.public_base}/?src=tg"


async def sync_chat_admins(bot: Bot, session: AsyncSession, group, chat_id: int) -> None:
    """Переносит админов чата в права бюджета."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except TelegramBadRequest:
        return
    await service.grant_admins(
        session,
        group_id=group.id,
        tg_user_ids=[a.user.id for a in admins if not a.user.is_bot],
    )


async def is_chat_admin(bot: Bot, chat_id: int, tg_user_id: int) -> bool:
    """Проверка по чату: админа могли назначить уже после входа в бюджет."""
    try:
        member = await bot.get_chat_member(chat_id, tg_user_id)
    except TelegramBadRequest:
        return False
    return member.status in ("administrator", "creator")


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
            if join:
                await sync_chat_admins(message.bot, session, group, message.chat.id)
        return group
    return await service.resolve_active_group(session, user)


NO_GROUP_HINT = texts.blocks(
    texts.heading(2, "💼 У вас пока нет общего бюджета"),
    texts.bullets(
        texts.join("Нажмите ", texts.bold("Создать бюджет"), " ниже — и позовите остальных"),
        texts.join(
            "Либо добавьте бота в общий чат и отправьте там ",
            texts.cmd("/join"),
            " — бюджет создастся сам",
        ),
    ),
)


def is_private(callback: CallbackQuery) -> bool:
    """Личный чат с ботом? Сообщения из inline-режима приватными не считаются."""
    return bool(callback.message and callback.message.chat.type == "private")


def _thread_id(message: Message) -> int | None:
    """Тема форума, если сообщение пришло из неё."""
    return message.message_thread_id if getattr(message, "is_topic_message", False) else None


GIF_DIR = BASE_DIR / "gifs"
_gif_ids: dict[str, str] = {}  # file_id уже загруженных роликов


async def send_reaction_gif(message: Message, name: str) -> None:
    """Ролик после записи операции: topup — пополнение, buy — покупка.

    Первый раз файл загружается, дальше отправляется по file_id: гонять один
    и тот же ролик в Telegram на каждую покупку незачем. Файла нет — молча
    пропускаем, операция уже записана и без него.
    """
    cached = _gif_ids.get(name)
    path = GIF_DIR / f"{name}.mp4"
    if cached is None and not path.exists():
        return

    try:
        sent = await message.bot.send_animation(
            chat_id=message.chat.id,
            message_thread_id=_thread_id(message),
            animation=cached or FSInputFile(path),
            disable_notification=True,  # уведомление придёт от самой карточки
        )
    except Exception as exc:
        # Ролик — украшение: что бы с ним ни случилось, карточку операции
        # это ломать не должно. Протухший file_id заодно выбрасываем.
        _gif_ids.pop(name, None)
        log.warning("Ролик %s не отправился (%s)", name, exc)
        return

    animation = getattr(sent, "animation", None)
    if animation is not None:
        _gif_ids[name] = animation.file_id


async def answer_rich(
    message: Message,
    markdown: object,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    reply: bool = False,
) -> Message:
    """Отправляет rich-сообщение: заголовки, таблицы, списки, сворачиваемые блоки.

    Это отдельный метод API (sendRichMessage), а не sendMessage с parse_mode.
    """
    return await message.bot.send_rich_message(
        chat_id=message.chat.id,
        message_thread_id=_thread_id(message),
        rich_message=InputRichMessage(markdown=str(markdown)),
        reply_markup=reply_markup,
        reply_parameters=(
            ReplyParameters(message_id=message.message_id) if reply else None
        ),
    )


async def edit_card(
    bot: Bot,
    callback: CallbackQuery,
    markdown: object,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Показывает rich-содержимое на месте нажатой кнопки.

    Сообщение с картинкой (диаграмма) текстом не правится — Telegram отвечает
    «there is no text in the message to edit», поэтому его заменяем новым.
    """
    rich = InputRichMessage(markdown=str(markdown))
    try:
        if callback.inline_message_id:
            await bot.edit_message_text(
                rich_message=rich,
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
            await bot.send_rich_message(
                chat_id=message.chat.id,
                message_thread_id=_thread_id(message),
                rich_message=rich,
                reply_markup=markup,
            )
            return

        await bot.edit_message_text(
            rich_message=rich,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=markup,
        )
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
    from app.bot import keyboards

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
