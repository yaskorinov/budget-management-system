"""Фильтры, специфичные для групповых чатов."""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message


class PromptReply(BaseFilter):
    """Принимает ответ на вопрос бота.

    В личке подходит любое сообщение. В группе — только reply на приглашение
    бота: во-первых, при включённом privacy mode обычные сообщения до бота не
    доходят вовсе, во-вторых, иначе состояние «жду сумму» съедало бы любую
    следующую реплику человека в чате.
    """

    async def __call__(self, message: Message, state: FSMContext) -> bool:
        if message.chat.type == "private":
            return True
        data = await state.get_data()
        prompt_id = data.get("prompt_id")
        reply = message.reply_to_message
        return bool(prompt_id and reply and reply.message_id == prompt_id)
