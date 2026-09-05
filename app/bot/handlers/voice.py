"""Голосовой ввод: /voice и голосовые сообщения.

Расшифровка идёт через модель с аудио на входе, дальше текст разбирается тем же
путём, что и напечатанный: сумма, категория, тип операции. Расшифровку всегда
показываем — модель может ослышаться, и человек должен видеть, что записано.
"""
from __future__ import annotations

import io

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards, texts
from app.bot.common import GROUP_CHATS, NO_GROUP_HINT, answer_rich, resolve_group
from app.bot.handlers.entry import (
    CONTRIBUTION_RE,
    record_contribution,
    record_purchase,
    source_for,
)
from app.config import settings
from app.core import voice as stt
from app.core.money import parse_amount
from app.db.models import User

router = Router(name="voice")

MAX_AUDIO_BYTES = 5 * 1024 * 1024  # голосовые длиннее пары минут не нужны


@router.message(Command("voice"))
async def voice_command(
    message: Message, session: AsyncSession, user: User
) -> None:
    if not settings.llm_enabled or not settings.llm_voice_model:
        await answer_rich(
            message,
            texts.join("Голосовой ввод не настроен: в .env нужны LLM_API_KEY и LLM_VOICE_MODEL."),
        )
        return

    group = await resolve_group(session, message, user)
    split = bool(group and group.is_split)
    await answer_rich(
        message,
        texts.blocks(
            texts.heading(2, "🎤 Голосовой ввод"),
            texts.join("Запишите голосовое — я расшифрую и запишу операцию."),
            texts.bullets(
                texts.join(texts.italic("«молоко хлеб восемьсот пятьдесят»"), " — покупка"),
                texts.join(
                    texts.italic("«внёс пять тысяч»"),
                    " — вернуть долг" if split else " — взнос в фонд",
                ),
            ),
            texts.italic("Расшифровку покажу — если ослышусь, поправите кнопками на карточке"),
        ),
    )


async def _download(bot: Bot, file_id: str) -> bytes | None:
    buffer = io.BytesIO()
    file = await bot.get_file(file_id)
    if file.file_size and file.file_size > MAX_AUDIO_BYTES:
        return None
    await bot.download_file(file.file_path, buffer)
    return buffer.getvalue()


@router.message(F.voice | F.audio)
async def voice_message(
    message: Message, session: AsyncSession, user: User, state: FSMContext, bot: Bot
) -> None:
    """Голосовое или аудио: расшифровываем и записываем операцию."""
    media = message.voice or message.audio
    if media is None:
        return

    # В группе реагируем только на явно адресованные боту голосовые: иначе
    # бот лез бы в каждую голосовую переписку
    if message.chat.type in GROUP_CHATS and not (
        message.reply_to_message and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == bot.id
    ):
        return

    if not settings.llm_enabled or not settings.llm_voice_model:
        await answer_rich(message, texts.join("Голосовой ввод не настроен."), reply=True)
        return

    group = await resolve_group(session, message, user)
    if group is None:
        await answer_rich(message, NO_GROUP_HINT)
        return

    # Расшифровка занимает секунды: без этого сообщения кажется, что бот молчит
    notice = await answer_rich(
        message,
        texts.join("🎤 ", texts.italic("Обрабатываю голосовое…")),
        reply=True,
    )

    async def say(markdown: object) -> None:
        """Дописывает то же сообщение, чтобы не плодить их в чате."""
        try:
            await bot.edit_message_text(
                rich_message=InputRichMessage(markdown=str(markdown)),
                chat_id=notice.chat.id,
                message_id=notice.message_id,
            )
        except TelegramBadRequest:
            await answer_rich(message, markdown, reply=True)

    await bot.send_chat_action(message.chat.id, "typing")
    audio = await _download(bot, media.file_id)
    if audio is None:
        await say(texts.join("Запись слишком длинная — уложитесь в пару минут."))
        return

    fmt = stt.audio_format(media.mime_type, getattr(media, "file_name", None))
    try:
        text = await stt.transcribe(audio, fmt)
    except stt.VoiceUnavailable:
        # Причина уже в логе: ключ, баланс, лимит. Человеку нужен выход, а не код ошибки
        await say(
            texts.blocks(
                texts.join("Распознавание сейчас недоступно."),
                texts.italic("Запишите операцию текстом: молоко хлеб 850"),
            )
        )
        return

    if not text:
        await say(
            texts.blocks(
                texts.join("Не разобрал запись."),
                texts.italic("Попробуйте ещё раз или напишите текстом: молоко хлеб 850"),
            )
        )
        return

    await state.clear()
    await say(texts.join("🎤 ", texts.italic("Услышал: ", text)))

    contribution = CONTRIBUTION_RE.match(text)
    rest = contribution.group("rest") if contribution else text
    amount, _ = parse_amount(rest)

    if contribution and amount:
        await record_contribution(
            message, session, user, group, amount, source_for(message)
        )
        return

    if amount is None:
        await answer_rich(
            message,
            texts.join("Не услышал сумму. Скажите ещё раз, например: молоко хлеб 850."),
            reply_markup=keyboards.cancel_kb() if message.chat.type == "private" else None,
        )
        return

    await record_purchase(message, session, user, group, text, source_for(message), bot)
