"""Статистика: круговые диаграммы и балансы."""
from __future__ import annotations

from aiogram import Bot, F, Router
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
from app.bot.common import NO_GROUP_HINT, edit_card, resolve_group
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
    markup = keyboards.stats_kb(report.mode, report.period)

    if report.is_empty:
        await message.answer(
            f"{_caption(report)}\n\n<i>За этот период расходов нет.</i>",
            reply_markup=markup,
        )
        return

    png = reports.render_png(report)
    if png is None:
        await message.answer(
            f"{_caption(report)}\n\n<code>{reports.render_text(report)}</code>",
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


@router.callback_query(StatsCB.filter())
async def stats_switch(
    callback: CallbackQuery,
    callback_data: StatsCB,
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    group = await service.resolve_active_group(session, user)
    if group is None:
        await callback.answer("Нет активной группы", show_alert=True)
        return

    report = await reports.build(
        session, group=group, mode=callback_data.mode, period=callback_data.period
    )
    markup = keyboards.stats_kb(report.mode, report.period)
    png = reports.render_png(report) if not report.is_empty else None

    # Сообщение с картинкой правим через media, текстовое — через текст.
    if png and callback.message and callback.message.photo:
        await callback.message.edit_media(
            InputMediaPhoto(
                media=BufferedInputFile(png, filename=f"stats-{report.mode}.png"),
                caption=_caption(report),
            ),
            reply_markup=markup,
        )
    elif png:
        await callback.message.answer_photo(
            BufferedInputFile(png, filename=f"stats-{report.mode}.png"),
            caption=_caption(report),
            reply_markup=markup,
        )
    else:
        body = (
            "<i>За этот период расходов нет.</i>"
            if report.is_empty
            else f"<code>{reports.render_text(report)}</code>"
        )
        text = f"{_caption(report)}\n\n{body}"
        if callback.message and callback.message.photo:
            await callback.message.edit_caption(caption=text, reply_markup=markup)
        else:
            await edit_card(bot, callback, text, markup)

    await callback.answer()
