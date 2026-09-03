"""Мидлвари: сессия БД и пользователь для каждого апдейта."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser

from app.core import service
from app.db.base import session_scope


class ContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with session_scope() as session:
            data["session"] = session
            tg_user: TgUser | None = data.get("event_from_user")
            if tg_user is not None and not tg_user.is_bot:
                data["user"] = await service.get_or_create_user(
                    session,
                    tg_user_id=tg_user.id,
                    first_name=tg_user.first_name or "",
                    last_name=tg_user.last_name,
                    username=tg_user.username,
                )
            return await handler(event, data)
