"""Подменённая сессия Telegram: запоминает вызовы, возвращает правдоподобные ответы."""
from __future__ import annotations
import datetime as dt
from aiogram.client.session.base import BaseSession
from aiogram.types import (
    Animation,
    Chat,
    ChatMemberMember,
    ChatMemberOwner,
    Message,
    PhotoSize,
    User as TgUser,
)

BOT_USER = TgUser(id=999, is_bot=True, first_name="Budget", username="budget_bot")


class MockSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []
        self.fail_on: set[str] = set()  # разовая имитация отказа Telegram
        self.chat_admins: set[int] = set()  # кого Telegram считает админом чата
        self._msg_id = 1000

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        data = method.model_dump(exclude_none=True)
        self.calls.append((name, data))

        if name in self.fail_on:
            from aiogram.exceptions import TelegramBadRequest

            self.fail_on.discard(name)
            raise TelegramBadRequest(
                method=method, message="Bad Request: BUTTON_TYPE_INVALID"
            )

        if name == "GetMe":
            return BOT_USER
        if name == "GetChatAdministrators":
            return [
                ChatMemberOwner(
                    user=TgUser(id=uid, is_bot=False, first_name="admin"),
                    is_anonymous=False,
                )
                for uid in self.chat_admins
            ]
        if name == "GetChatMember":
            uid = data.get("user_id")
            user = TgUser(id=uid, is_bot=False, first_name="user")
            if uid in self.chat_admins:
                return ChatMemberOwner(user=user, is_anonymous=False)
            return ChatMemberMember(user=user)
        if name == "SendAnimation":
            self._msg_id += 1
            return Message(
                message_id=self._msg_id,
                date=dt.datetime.now(),
                chat=Chat(id=data.get("chat_id", 1), type="private"),
                from_user=BOT_USER,
                animation=Animation(
                    file_id="anim-id", file_unique_id="anim", width=320,
                    height=320, duration=2,
                ),
            )
        if name in {"SendMessage", "SendRichMessage", "SendPhoto", "EditMessageText",
                    "EditMessageMedia", "EditMessageCaption", "EditMessageReplyMarkup"}:
            if data.get("inline_message_id"):
                return True
            self._msg_id += 1
            photo = (
                [PhotoSize(file_id="f", file_unique_id="u", width=800, height=450)]
                if name == "SendPhoto"
                else None
            )
            animation = (
                Animation(file_id="anim-id", file_unique_id="anim", width=320,
                          height=320, duration=2)
                if (data.get("rich_message") or {}).get("media")
                else None
            )
            return Message(
                message_id=self._msg_id,
                date=dt.datetime.now(),
                chat=Chat(id=data.get("chat_id", 1), type="private"),
                from_user=BOT_USER,
                animation=animation,
                text=None if photo else (
                    data.get("text")
                    or (data.get("rich_message") or {}).get("markdown")
                    or ""
                ),
                caption=data.get("caption") if photo else None,
                photo=photo,
            )
        return True

    def find(self, name):
        return [data for called, data in self.calls if called == name]

    def texts(self):
        out = []
        for name, data in self.calls:
            if name in {"SendMessage", "SendRichMessage", "EditMessageText"}:
                out.append(
                    data.get("text")
                    or (data.get("rich_message") or {}).get("markdown", "")
                )
            elif name in {"SendPhoto", "EditMessageCaption"}:
                out.append("[PHOTO] " + (data.get("caption") or ""))
            elif name == "AnswerInlineQuery":
                out.append("[INLINE] " + " | ".join(
                    r.get("title", "") for r in data.get("results", [])))
            elif name == "AnswerCallbackQuery":
                if data.get("text"):
                    out.append("[TOAST] " + data["text"])
        return out

    def reset(self):
        self.calls.clear()
        self.fail_on.clear()
