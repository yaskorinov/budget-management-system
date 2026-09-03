"""Inline-режим: операции и статистика прямо из строки ввода любого чата."""
from __future__ import annotations

import re

from aiogram import Bot, F, Router
from aiogram.types import (
    CallbackQuery,
    ChosenInlineResult,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultPhoto,
    InlineQueryResultsButton,
    InputRichMessage,
    InputRichMessageContent,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import drafts, keyboards, texts
from app.bot.callbacks import DraftCB
from app.bot.common import edit_card
from app.bot.handlers.entry import CONTRIBUTION_RE
from app.config import settings
from app.core import categories as cat
from app.core import periods, reports, service
from app.core.classifier import parse_purchase
from app.core.money import format_money, parse_amount
from app.db.models import Group, User

router = Router(name="inline")

STATS_RE = re.compile(r"^\s*(?:стат\w*|stat\w*|диаграмм\w*)\s*(?P<rest>.*)$", re.IGNORECASE)
BALANCE_RE = re.compile(r"^\s*(?:баланс\w*|фонд|итог\w*|balance)\s*$", re.IGNORECASE)


def _article(
    *,
    result_id: str,
    title: str,
    description: str,
    text: object,
    markup=None,
) -> InlineQueryResultArticle:
    """Результат inline-запроса, который отправит rich-сообщение."""
    return InlineQueryResultArticle(
        id=result_id,
        title=title,
        description=description,
        input_message_content=InputRichMessageContent(
            rich_message=InputRichMessage(markdown=str(text))
        ),
        reply_markup=markup,
    )


def _hints(group_title: str) -> list[InlineQueryResultArticle]:
    return [
        _article(
            result_id="hint-buy",
            title="🛒 Покупка: напишите что и сколько",
            description="например: молоко хлеб 850",
            text=texts.join("Бюджет «", group_title, "»: напишите покупку с суммой"),
        ),
        _article(
            result_id="hint-add",
            title="💰 Взнос: «внёс 5000»",
            description="пополнить общий фонд",
            text=texts.join("Бюджет «", group_title, "»: напишите «внёс 5000»"),
        ),
        _article(
            result_id="hint-stats",
            title="📊 «стата категории» или «стата люди»",
            description="круговая диаграмма расходов",
            text=texts.join("Бюджет «", group_title, "»: напишите «стата категории»"),
        ),
        _article(
            result_id="hint-balance",
            title="💼 «баланс»",
            description="кто сколько внёс и сколько должен",
            text=texts.join("Бюджет «", group_title, "»: напишите «баланс»"),
        ),
    ]


async def _stats_results(
    session: AsyncSession, group: Group, rest: str
) -> list[InlineQueryResultArticle | InlineQueryResultPhoto]:
    words = rest.split()
    mode = reports.normalize_mode(words[0] if words else None)
    period = periods.normalize(" ".join(words[1:]) if len(words) > 1 else None)

    report = await reports.build(session, group=group, mode=mode, period=period)
    caption = texts.stats_caption(
        group_title=group.title,
        mode=report.mode,
        period_title=report.period_title,
        total=report.total,
    )
    photo_caption = texts.stats_caption_plain(
        group_title=group.title,
        mode=report.mode,
        period_title=report.period_title,
        total=report.total,
    )

    if report.is_empty:
        return [
            _article(
                result_id=f"stats-empty-{report.mode}-{report.period}",
                title=f"📊 Расходы {reports.MODES[report.mode]}",
                description=f"{report.period_title}: расходов нет",
                text=texts.blocks(
                    caption, texts.italic("За этот период расходов нет")
                ),
            )
        ]

    png = reports.render_png(report)
    if png and settings.public_base:
        url = f"{settings.public_base}/charts/{reports.save_png(png)}"
        return [
            InlineQueryResultPhoto(
                id=f"stats-{report.mode}-{report.period}",
                photo_url=url,
                thumbnail_url=url,
                title=f"Расходы {reports.MODES[report.mode]}",
                description=f"{report.period_title}, {format_money(report.total)}",
                caption=photo_caption,
            )
        ]

    return [
        _article(
            result_id=f"stats-text-{report.mode}-{report.period}",
            title=f"📊 Расходы {reports.MODES[report.mode]}",
            description=f"{report.period_title}, {format_money(report.total)}",
            text=texts.blocks(caption, texts.stats_table(report.slices, report.total)),
        )
    ]


def _draft_result(draft: drafts.Draft, group: Group, author: User):
    card = texts.draft_card(
        kind=draft.kind,
        amount=draft.amount,
        title=draft.title,
        category=draft.category,
        group_title=group.title,
        category_source=draft.category_source,
        author_name=author.short_name,
    )
    if draft.kind == "contribution":
        title = f"💰 Взнос {format_money(draft.amount)}"
        description = f"в фонд «{group.title}»"
    else:
        category = cat.get(draft.category)
        title = f"{category.emoji} {format_money(draft.amount)} — {draft.title}"
        description = f"{category.title} · «{group.title}»"

    return _article(
        result_id=draft.id,
        title=title,
        description=description,
        text=card,
        markup=keyboards.draft_kb(
            draft.id, with_category=(draft.kind == "purchase")
        ),
    )


@router.inline_query()
async def inline_query(
    query: InlineQuery, session: AsyncSession, user: User, bot: Bot
) -> None:
    group = await service.resolve_active_group(session, user)
    open_bot = InlineQueryResultsButton(text="Открыть бота", start_parameter="setup")

    if group is None:
        await query.answer(
            [
                _article(
                    result_id="no-group",
                    title="Нет активного бюджета",
                    description="Откройте бота и создайте или выберите группу",
                    text=texts.join(
                        "Сначала нужно создать общий бюджет — напишите боту /start"
                    ),
                )
            ],
            cache_time=1,
            is_personal=True,
            button=open_bot,
        )
        return

    text = query.query.strip()

    if not text:
        await query.answer(
            _hints(group.title), cache_time=5, is_personal=True, button=open_bot
        )
        return

    if match := STATS_RE.match(text):
        results = await _stats_results(session, group, match.group("rest"))
        await query.answer(results, cache_time=10, is_personal=True, button=open_bot)
        return

    if BALANCE_RE.match(text):
        data = await service.summary(session, group=group)
        await query.answer(
            [
                _article(
                    result_id=f"balance-{group.id}",
                    title="💼 Балансы участников",
                    description=f"в фонде {format_money(data.fund_left)}",
                    text=texts.summary_text(data),
                )
            ],
            cache_time=5,
            is_personal=True,
            button=open_bot,
        )
        return

    if match := CONTRIBUTION_RE.match(text):
        amount, _ = parse_amount(match.group("rest"))
        if amount:
            draft = drafts.put(
                drafts.Draft(
                    id=drafts.make_id(user.tg_user_id, f"c:{text}"),
                    tg_user_id=user.tg_user_id,
                    group_id=group.id,
                    kind="contribution",
                    amount=amount,
                    raw_text=text,
                )
            )
            await query.answer(
                [_draft_result(draft, group, user)],
                cache_time=1,
                is_personal=True,
                button=open_bot,
            )
            return

    parsed = await parse_purchase(text)
    if not parsed.amount:
        await query.answer(
            [
                _article(
                    result_id="need-amount",
                    title="Добавьте сумму",
                    description="например: молоко хлеб 850",
                    text=texts.join(
                        "Чтобы записать покупку, укажите сумму: «молоко хлеб 850»"
                    ),
                )
            ],
            cache_time=1,
            is_personal=True,
            button=open_bot,
        )
        return

    draft = drafts.put(
        drafts.Draft(
            id=drafts.make_id(user.tg_user_id, f"p:{text}"),
            tg_user_id=user.tg_user_id,
            group_id=group.id,
            kind="purchase",
            amount=parsed.amount,
            title=parsed.title,
            category=parsed.category,
            category_source=parsed.source,
            raw_text=text,
        )
    )
    await query.answer(
        [_draft_result(draft, group, user)],
        cache_time=1,
        is_personal=True,
        button=open_bot,
    )


# --------------------------------------------------------------------------- #
#  Подтверждение черновика
# --------------------------------------------------------------------------- #


async def _commit(session: AsyncSession, draft: drafts.Draft, user: User):
    """Записывает черновик в базу. Повторный вызов вернёт ту же операцию."""
    if draft.operation_id:
        return await service.get_operation(session, draft.operation_id)

    if draft.kind == "contribution":
        operation = await service.add_contribution(
            session,
            group_id=draft.group_id,
            author_id=user.id,
            amount=draft.amount,
            source="inline",
        )
    else:
        operation = await service.add_purchase(
            session,
            group_id=draft.group_id,
            author_id=user.id,
            amount=draft.amount,
            category=draft.category,
            title=draft.title,
            participant_ids=draft.participant_ids,
            source="inline",
            category_source=draft.category_source,
            raw_text=draft.raw_text,
        )
    draft.operation_id = operation.id
    return operation


async def _render_committed(session: AsyncSession, operation) -> tuple[str, object]:
    group = await session.get(Group, operation.group_id)
    members = await service.group_members(session, operation.group_id)
    data = await service.summary(session, group=group)
    card = texts.operation_card(
        operation, group=group, members_total=len(members), fund_left=data.fund_left
    )
    return card, keyboards.operation_kb(operation, compact=True)


@router.chosen_inline_result()
async def chosen_result(
    chosen: ChosenInlineResult, session: AsyncSession, user: User, bot: Bot
) -> None:
    """Срабатывает, если в BotFather включён inline feedback — тогда без кнопки."""
    draft = drafts.get(chosen.result_id)
    if draft is None or draft.tg_user_id != user.tg_user_id or draft.operation_id:
        return

    operation = await _commit(session, draft, user)
    if operation is None or not chosen.inline_message_id:
        return
    card, markup = await _render_committed(session, operation)
    await bot.edit_message_text(
        text=card, inline_message_id=chosen.inline_message_id, reply_markup=markup
    )


@router.callback_query(DraftCB.filter(F.action == "ok"))
async def draft_confirm(
    callback: CallbackQuery, callback_data: DraftCB, session: AsyncSession, user: User, bot: Bot
) -> None:
    draft = drafts.get(callback_data.draft_id)
    if draft is None:
        await callback.answer("Черновик устарел — отправьте операцию заново", show_alert=True)
        return
    if draft.tg_user_id != user.tg_user_id:
        await callback.answer("Подтвердить может только автор записи", show_alert=True)
        return

    operation = await _commit(session, draft, user)
    if operation is None:
        await callback.answer("Операция уже удалена", show_alert=True)
        return

    card, markup = await _render_committed(session, operation)
    await edit_card(bot, callback, card, markup)
    await callback.answer("Записано")


@router.callback_query(DraftCB.filter(F.action == "cancel"))
async def draft_cancel(
    callback: CallbackQuery, callback_data: DraftCB, user: User, bot: Bot
) -> None:
    draft = drafts.get(callback_data.draft_id)
    if draft and draft.tg_user_id != user.tg_user_id:
        await callback.answer("Отменить может только автор записи", show_alert=True)
        return
    if draft and draft.operation_id:
        await callback.answer("Операция уже записана — удалите её кнопкой 🗑", show_alert=True)
        return

    drafts.drop(callback_data.draft_id)
    await edit_card(bot, callback, texts.italic("Черновик отменён"), None)
    await callback.answer()


@router.callback_query(DraftCB.filter(F.action.in_({"cat", "back"})))
async def draft_categories(
    callback: CallbackQuery, callback_data: DraftCB, session: AsyncSession, user: User, bot: Bot
) -> None:
    draft = drafts.get(callback_data.draft_id)
    if draft is None:
        await callback.answer("Черновик устарел", show_alert=True)
        return
    if draft.tg_user_id != user.tg_user_id:
        await callback.answer("Категорию меняет автор записи", show_alert=True)
        return

    if callback_data.action == "back":
        group = await session.get(Group, draft.group_id)
        await edit_card(
            bot,
            callback,
            texts.draft_card(
                kind=draft.kind,
                amount=draft.amount,
                title=draft.title,
                category=draft.category,
                group_title=group.title,
                category_source=draft.category_source,
                author_name=user.short_name,
            ),
            keyboards.draft_kb(draft.id),
        )
    else:
        await edit_card(
            bot,
            callback,
            texts.join(
                "Выберите категорию для «", draft.title, "» · ",
                format_money(draft.amount),
            ),
            keyboards.draft_categories_kb(draft.id),
        )
    await callback.answer()


@router.callback_query(DraftCB.filter(F.action == "setcat"))
async def draft_set_category(
    callback: CallbackQuery, callback_data: DraftCB, session: AsyncSession, user: User, bot: Bot
) -> None:
    draft = drafts.get(callback_data.draft_id)
    if draft is None:
        await callback.answer("Черновик устарел", show_alert=True)
        return
    if draft.tg_user_id != user.tg_user_id:
        await callback.answer("Категорию меняет автор записи", show_alert=True)
        return

    draft.category = callback_data.value
    draft.category_source = "manual"
    group = await session.get(Group, draft.group_id)
    await edit_card(
        bot,
        callback,
        texts.draft_card(
            kind=draft.kind,
            amount=draft.amount,
            title=draft.title,
            category=draft.category,
            group_title=group.title,
            category_source=draft.category_source,
            author_name=user.short_name,
        ),
        keyboards.draft_kb(draft.id),
    )
    await callback.answer("Категория обновлена")
