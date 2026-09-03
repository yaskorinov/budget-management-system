"""Статистика: круговые диаграммы и балансы."""
from __future__ import annotations

from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InputMediaPhoto,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards, texts
from app.bot.callbacks import MenuCB, StatsCB
from app.bot.common import (
    NO_GROUP_HINT,
    edit_card,
    group_for_callback,
    is_private,
    resolve_group,
)
from app.core import periods, reports, service
from app.db.models import Group, User

router = Router(name="stats")


def _caption(report: reports.Report) -> str:
    return texts.stats_caption(
        group_title=report.group.title,
        mode=report.mode,
        period_title=report.period_title,
        total=report.total,
    )


async def send_report(
    message: Message, session: AsyncSession, group: Group, mode: str, period: str
) -> None:
    report = await reports.build(session, group=group, mode=mode, period=period)
    markup = keyboards.stats_kb(
        report.mode, report.period, private=message.chat.type == "private"
    )

    if report.is_empty:
        await message.answer(
            texts.lines(
                _caption(report),
                texts.join(""),
                texts.italic("За этот период расходов нет"),
            ),
            reply_markup=markup,
        )
        return

    png = reports.render_png(report)
    if png is None:
        await message.answer(
            texts.lines(_caption(report), texts.pre(reports.render_text(report))),
            reply_markup=markup,
        )
        return

    await message.answer_photo(
        BufferedInputFile(png, filename=f"stats-{report.mode}.png"),
        caption=_caption(report),
        reply_markup=markup,
    )


@router.message(Command("stats"))
async def stats_command(
    message: Message, command: CommandObject, session: AsyncSession, user: User
) -> None:
    group = await resolve_group(session, message, user)
    if group is None:
        await message.answer(NO_GROUP_HINT)
        return

    args = (command.args or "").split()
    mode = reports.normalize_mode(args[0] if args else None)
    period = periods.normalize(" ".join(args[1:]) if len(args) > 1 else None)
    await send_report(message, session, group, mode, period)


@router.message(Command("balance"))
async def balance_command(message: Message, session: AsyncSession, user: User) -> None:
    group = await resolve_group(session, message, user)
    if group is None:
        await message.answer(NO_GROUP_HINT)
        return
    data = await service.summary(session, group=group)
    await message.answer(texts.summary_text(data))


@router.callback_query(MenuCB.filter(F.action == "stats"))
async def menu_stats(
    callback: CallbackQuery, session: AsyncSession, user: User, bot: Bot
) -> None:
    group = await service.resolve_active_group(session, user)
    if group is None:
        await edit_card(bot, callback, NO_GROUP_HINT, keyboards.back_home_kb())
        await callback.answer()
        return
    await callback.answer()
    await send_report(callback.message, session, group, "categories", "month")


def _already_selected(callback: CallbackQuery, target: StatsCB) -> bool:
    """Нажали на кнопку, которая и так активна?

    Активные помечены «• ». Перерисовывать нечем: Telegram отклоняет правку
    неизменившимся содержимым, и пользователь видел бы ложную ошибку.
    """
    markup = getattr(callback.message, "reply_markup", None)
    if markup is None:
        return False
    pressed = target.pack()
    return any(
        button.callback_data == pressed and (button.text or "").startswith("• ")
        for row in markup.inline_keyboard
        for button in row
    )


@router.callback_query(StatsCB.filter())
async def stats_switch(
    callback: CallbackQuery,
    callback_data: StatsCB,
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    if _already_selected(callback, callback_data):
        await callback.answer("Уже показано")
        return

    group = await group_for_callback(session, callback, user)
    if group is None:
        await callback.answer("Нет активной группы", show_alert=True)
        return

    report = await reports.build(
        session, group=group, mode=callback_data.mode, period=callback_data.period
    )
    markup = keyboards.stats_kb(report.mode, report.period, private=is_private(callback))
    png = reports.render_png(report) if not report.is_empty else None

    message = callback.message
    was_photo = bool(getattr(message, "photo", None))
    caption = _caption(report)

    try:
        if png and was_photo:
            await message.edit_media(
                InputMediaPhoto(
                    media=BufferedInputFile(png, filename=f"stats-{report.mode}.png"),
                    caption=caption,
                ),
                reply_markup=markup,
            )
        elif png:
            # Текстовое сообщение картинкой не станет — заменяем его.
            with suppress(TelegramBadRequest):
                await message.delete()
            await bot.send_photo(
                message.chat.id,
                BufferedInputFile(png, filename=f"stats-{report.mode}.png"),
                caption=caption,
                reply_markup=markup,
            )
        else:
            body = (
                texts.italic("За этот период расходов нет")
                if report.is_empty
                else texts.pre(reports.render_text(report))
            )
            # Диаграмму за пустой период оставлять нельзя: старая картинка
            # с новой подписью выглядит как настоящие данные.
            await edit_card(bot, callback, texts.lines(caption, body), markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise

    await callback.answer()
