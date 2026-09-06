"""Единый процесс: FastAPI (API + мини-аппа) и бот в одном событийном цикле."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from aiogram.types import Update

from app.api.routes import charts_router, oauth_router, router
from app.bot.bot import create_bot, create_dispatcher, setup_bot_commands
from app.bot.scheduler import daily_loop
from app.config import BASE_DIR, proxy_warning, settings
from app.db.base import init_db

log = logging.getLogger(__name__)

WEB_DIR = BASE_DIR / "app" / "web"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()

    if warning := proxy_warning(settings):
        log.warning(warning)

    app.state.bot = None
    app.state.dispatcher = None
    bot = None
    polling: asyncio.Task | None = None
    daily: asyncio.Task | None = None
    dispatcher = None

    try:
        bot = create_bot()
        dispatcher = create_dispatcher()
        app.state.bot = bot
        app.state.dispatcher = dispatcher
        allowed = dispatcher.resolve_used_update_types()

        try:
            await setup_bot_commands(bot)
        except Exception as exc:  # Telegram недоступен — не повод не поднимать веб
            log.warning("Не удалось выставить команды бота: %s", exc)

        if settings.bot_mode == "webhook":
            if not settings.public_base:
                raise RuntimeError("BOT_MODE=webhook требует PUBLIC_BASE_URL")
            await bot.set_webhook(
                settings.webhook_url,
                secret_token=settings.webhook_secret,
                allowed_updates=allowed,
                drop_pending_updates=True,
            )
            log.info("Вебхук установлен: %s", settings.webhook_url)
        else:
            await bot.delete_webhook(drop_pending_updates=True)
            polling = asyncio.create_task(
                dispatcher.start_polling(bot, allowed_updates=allowed)
            )
            log.info("Бот запущен в режиме long polling")

        daily = asyncio.create_task(daily_loop(bot))
    except Exception as exc:
        log.error("Бот не запустился (%s). Веб-часть продолжает работать.", exc)

    try:
        yield
    finally:
        if daily is not None:
            daily.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await daily
        if polling is not None and dispatcher is not None:
            await dispatcher.stop_polling()
            polling.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await polling
        if bot is not None:
            await bot.session.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Общий бюджет",
        description="Учёт совместных расходов: бот, мини-аппа и веб",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # API защищён токеном, куки не используются
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/api")
    app.include_router(charts_router)
    app.include_router(oauth_router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(settings.webhook_path, include_in_schema=False)
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str = Header(default=""),
    ) -> dict[str, bool]:
        if x_telegram_bot_api_secret_token != settings.webhook_secret:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Bad secret token")
        bot = request.app.state.bot
        dispatcher = request.app.state.dispatcher
        if bot is None or dispatcher is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Бот не запущен")
        update = Update.model_validate(await request.json(), context={"bot": bot})
        await dispatcher.feed_update(bot, update)
        return {"ok": True}

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


app = create_app()
