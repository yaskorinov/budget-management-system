"""Сборка бота: диспетчер, роутеры, команды меню."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from app.bot.handlers import entry, fallback, inline, menu, operations, stats
from app.bot.middlewares import ContextMiddleware
from app.config import settings

log = logging.getLogger(__name__)

PRIVATE_COMMANDS = [
    BotCommand(command="start", description="Меню и балансы"),
    BotCommand(command="add", description="Внести деньги в фонд"),
    BotCommand(command="buy", description="Записать покупку"),
    BotCommand(command="balance", description="Балансы участников"),
    BotCommand(command="stats", description="Диаграмма расходов"),
    BotCommand(command="ops", description="Мои операции"),
    BotCommand(command="groups", description="Выбрать активный бюджет"),
    BotCommand(command="newgroup", description="Создать бюджет"),
    BotCommand(command="web", description="Ссылка на веб-версию"),
    BotCommand(command="help", description="Как пользоваться"),
]

GROUP_COMMANDS = [
    BotCommand(command="join", description="Присоединиться к бюджету чата"),
    BotCommand(command="add", description="Внести деньги в фонд"),
    BotCommand(command="buy", description="Записать покупку"),
    BotCommand(command="balance", description="Балансы участников"),
    BotCommand(command="stats", description="Диаграмма расходов"),
    BotCommand(command="ops", description="Последние операции"),
    BotCommand(command="members", description="Участники"),
    BotCommand(command="help", description="Как пользоваться"),
]


def create_bot() -> Bot:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN не задан — заполните .env")
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.outer_middleware(ContextMiddleware())

    # Порядок важен: команды и состояния -> инлайн -> свободный текст.
    dispatcher.include_router(menu.router)
    dispatcher.include_router(entry.router)
    dispatcher.include_router(operations.router)
    dispatcher.include_router(stats.router)
    dispatcher.include_router(inline.router)
    dispatcher.include_router(fallback.router)
    return dispatcher


async def setup_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())
