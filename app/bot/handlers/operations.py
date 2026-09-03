"""Список операций, карточка, правка и удаление."""
from __future__ import annotations

from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards, texts
from app.bot.callbacks import MenuCB, OpCB, OpsPageCB
from app.bot.common import (
    NO_GROUP_HINT,
    answer_rich,
    edit_card,
    group_for_callback,
    is_private,
    resolve_group,
    show_operation_card,
)
from app.bot.filters import PromptReply
from app.bot.states import EditOperation
from app.core import service
from app.core.money import parse_amount
from app.db.models import Group, User

router = Router(name="operations")

PAGE = 10


async def render_operations(
    session: AsyncSession, user: User, group: Group, *, scope: str, offset: int
) -> tuple[str, object]:
    author_id = user.id if scope == "mine" else None
    operations = await service.list_operations(
        session, group_id=group.id, author_id=author_id, limit=PAGE, offset=offset
    )
    total = await service.count_operations(session, group_id=group.id, author_id=author_id)
    scope_title = "Мои операции" if scope == "mine" else "Операции группы"
    subtitle = group.title
    if total:
        subtitle += f" · {offset + 1}–{offset + len(operations)} из {total}"

    text = texts.operations_text(
        operations,
        title=texts.heading(2, texts.join("📒 ", scope_title)),
        subtitle=subtitle,
        empty="Пока пусто. Добавьте взнос или покупку",
    )
    text = texts.blocks(
        text, texts.italic("Нажмите номер операции, чтобы открыть карточку с правкой")
    )
    markup = keyboards.ops_kb(
        operations, scope=scope, offset=offset, has_more=offset + PAGE < total, page=PAGE
    )
    return text, markup


@router.message(Command("ops"))
async def ops_command(message: Message, session: AsyncSession, user: User) -> None:
    group = await resolve_group(session, message, user)
    if group is None:
        await answer_rich(message, NO_GROUP_HINT)
        return
    scope = "all" if message.chat.type != "private" else "mine"
    text, markup = await render_operations(session, user, group, scope=scope, offset=0)
    await answer_rich(message, text, reply_markup=markup)


@router.callback_query(MenuCB.filter(F.action == "ops"))
async def menu_ops(
    callback: CallbackQuery, session: AsyncSession, user: User, bot: Bot
) -> None:
    group = await service.resolve_active_group(session, user)
    if group is None:
        await edit_card(bot, callback, NO_GROUP_HINT, keyboards.back_home_kb())
        await callback.answer()
        return
    text, markup = await render_operations(session, user, group, scope="mine", offset=0)
    await edit_card(bot, callback, text, markup)
    await callback.answer()


@router.callback_query(OpsPageCB.filter())
async def ops_page(
    callback: CallbackQuery,
    callback_data: OpsPageCB,
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    group = await group_for_callback(session, callback, user)
    if group is None:
        await callback.answer("Нет активной группы", show_alert=True)
        return
    text, markup = await render_operations(
        session, user, group, scope=callback_data.scope, offset=callback_data.offset
    )
    await edit_card(bot, callback, text, markup)
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Карточка операции и её правка
# --------------------------------------------------------------------------- #


async def _load(
    callback: CallbackQuery, session: AsyncSession, user: User, op_id: int, *, write: bool
):
    """Достаёт операцию и проверяет права. None — доступ закрыт, ответ уже отправлен."""
    operation = await service.get_operation(session, op_id)
    if operation is None:
        await callback.answer("Операция не найдена или удалена", show_alert=True)
        return None
    if not await service.is_member(
        session, group_id=operation.group_id, user_id=user.id
    ):
        await callback.answer("Это операция чужой группы", show_alert=True)
        return None
    if write and not await service.can_manage(session, operation, user):
        await callback.answer(
            "Править операцию может только тот, кто её внёс (или админ группы)",
            show_alert=True,
        )
        return None
    return operation


@router.callback_query(OpCB.filter(F.action == "card"))
async def op_card(
    callback: CallbackQuery, callback_data: OpCB, session: AsyncSession, user: User, bot: Bot
) -> None:
    operation = await _load(callback, session, user, callback_data.op_id, write=False)
    if operation is None:
        return
    await show_operation_card(bot, callback, session, operation)
    await callback.answer()


@router.callback_query(OpCB.filter(F.action == "cat"))
async def op_categories(
    callback: CallbackQuery, callback_data: OpCB, session: AsyncSession, user: User, bot: Bot
) -> None:
    operation = await _load(callback, session, user, callback_data.op_id, write=True)
    if operation is None:
        return
    await edit_card(
        bot,
        callback,
        texts.join(
            "Выберите категорию для ", texts.code("#" + str(operation.id)), " · ",
            texts.money(operation.amount), " — ", operation.title or "",
        ),
        keyboards.op_categories_kb(operation.id),
    )
    await callback.answer()


@router.callback_query(OpCB.filter(F.action == "setcat"))
async def op_set_category(
    callback: CallbackQuery, callback_data: OpCB, session: AsyncSession, user: User, bot: Bot
) -> None:
    operation = await _load(callback, session, user, callback_data.op_id, write=True)
    if operation is None:
        return
    await service.edit_operation(session, operation, category=callback_data.value)
    await show_operation_card(
        bot, callback, session, operation, header=texts.join("✏️ ", texts.bold("Категория обновлена"))
    )
    await callback.answer("Категория обновлена")


@router.callback_query(OpCB.filter(F.action == "parts"))
async def op_participants(
    callback: CallbackQuery, callback_data: OpCB, session: AsyncSession, user: User, bot: Bot
) -> None:
    operation = await _load(callback, session, user, callback_data.op_id, write=True)
    if operation is None:
        return
    members = await service.group_members(session, operation.group_id)
    await edit_card(
        bot,
        callback,
        texts.lines(
            texts.join(
                "Между кем делится ", texts.code("#" + str(operation.id)),
                " · ", texts.money(operation.amount), "?",
            ),
            texts.italic("Нажмите, чтобы включить или исключить"),
        ),
        keyboards.op_participants_kb(operation, members),
    )
    await callback.answer()


@router.callback_query(OpCB.filter(F.action == "toggle"))
async def op_toggle_participant(
    callback: CallbackQuery, callback_data: OpCB, session: AsyncSession, user: User, bot: Bot
) -> None:
    operation = await _load(callback, session, user, callback_data.op_id, write=True)
    if operation is None:
        return

    target = int(callback_data.value)
    current = [share.user_id for share in operation.shares]
    if target in current:
        if len(current) == 1:
            await callback.answer("Нужен хотя бы один участник", show_alert=True)
            return
        current.remove(target)
    else:
        current.append(target)

    await service.edit_operation(session, operation, participant_ids=current)
    members = await service.group_members(session, operation.group_id)
    await edit_card(
        bot,
        callback,
        texts.lines(
            texts.join(
                "Между кем делится ", texts.code("#" + str(operation.id)),
                " · ", texts.money(operation.amount), "?",
            ),
            texts.join("Сейчас по ", texts.bold(texts.money(operation.shares[0].amount)), " с человека"),
        ),
        keyboards.op_participants_kb(operation, members),
    )
    await callback.answer()


@router.callback_query(OpCB.filter(F.action == "amount"))
async def op_amount_prompt(
    callback: CallbackQuery,
    callback_data: OpCB,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    bot: Bot,
) -> None:
    operation = await _load(callback, session, user, callback_data.op_id, write=True)
    if operation is None:
        return

    await state.set_state(EditOperation.amount)
    # prompt_id — карточка, на которую нужно ответить: в группе бот принимает
    # только reply на своё сообщение (см. PromptReply).
    await state.set_data(
        {
            "op_id": operation.id,
            "inline_message_id": callback.inline_message_id,
            "prompt_id": callback.message.message_id if callback.message else None,
            "prompt_chat_id": callback.message.chat.id if callback.message else None,
        }
    )

    if callback.inline_message_id:
        where = "Отправьте новую сумму боту в личку"
    elif is_private(callback):
        where = "Отправьте новую сумму сообщением"
    else:
        where = "Ответьте на это сообщение новой суммой"

    await edit_card(
        bot,
        callback,
        texts.lines(
            texts.join("✏️ Новая сумма для ", texts.code("#" + str(operation.id))),
            texts.join("Сейчас: ", texts.bold(texts.money(operation.amount))),
            texts.italic(where),
        ),
        keyboards.operation_kb(operation, compact=True),
    )
    await callback.answer()


@router.message(EditOperation.amount, PromptReply(), F.text)
async def op_amount_set(
    message: Message, session: AsyncSession, user: User, state: FSMContext, bot: Bot
) -> None:
    amount, _ = parse_amount(message.text)
    if amount is None:
        await answer_rich(message, reply=True, markdown=
            texts.join("Не понял сумму. Напишите числом, например ", texts.code("850"))
        )
        return

    data = await state.get_data()
    await state.clear()

    operation = await service.get_operation(session, int(data.get("op_id") or 0))
    if operation is None or not await service.can_manage(session, operation, user):
        await answer_rich(message, reply=True, markdown=texts.join("Операция не найдена или недоступна для правки."))
        return

    await service.edit_operation(session, operation, amount=amount)
    group = await session.get(Group, operation.group_id)
    members = await service.group_members(session, operation.group_id)
    card = texts.operation_card(
        operation,
        group=group,
        header=texts.join("✏️ ", texts.bold("Сумма обновлена")),
        members_total=len(members),
    )
    markup = keyboards.operation_kb(operation, compact=True)

    # Правим ту же карточку, чтобы в чате не оставалось устаревшей копии.
    updated = False
    if data.get("inline_message_id"):
        with suppress(TelegramBadRequest):
            await bot.edit_message_text(
                text=card, inline_message_id=data["inline_message_id"], reply_markup=markup
            )
            updated = True
    elif data.get("prompt_chat_id") and data.get("prompt_id"):
        with suppress(TelegramBadRequest):
            await bot.edit_message_text(
                text=card,
                chat_id=data["prompt_chat_id"],
                message_id=data["prompt_id"],
                reply_markup=markup,
            )
            updated = True

    if updated:
        await answer_rich(message, reply=True, markdown=
            texts.join(
                "✏️ ", texts.code("#" + str(operation.id)), " — теперь ",
                texts.bold(texts.money(operation.amount)),
            )
        )
    else:
        await answer_rich(message, card, reply_markup=markup)


@router.callback_query(OpCB.filter(F.action == "del"))
async def op_delete_confirm(
    callback: CallbackQuery, callback_data: OpCB, session: AsyncSession, user: User, bot: Bot
) -> None:
    operation = await _load(callback, session, user, callback_data.op_id, write=True)
    if operation is None:
        return
    await edit_card(
        bot,
        callback,
        texts.lines(
            texts.join(
                "Удалить ", texts.code("#" + str(operation.id)), " на ",
                texts.bold(texts.money(operation.amount)), "?",
            ),
            texts.italic("Она исчезнет из балансов и статистики"),
        ),
        keyboards.confirm_delete_kb(operation.id),
    )
    await callback.answer()


@router.callback_query(OpCB.filter(F.action == "delyes"))
async def op_delete(
    callback: CallbackQuery, callback_data: OpCB, session: AsyncSession, user: User, bot: Bot
) -> None:
    operation = await _load(callback, session, user, callback_data.op_id, write=True)
    if operation is None:
        return

    group = await session.get(Group, operation.group_id)
    await service.delete_operation(session, operation)
    data = await service.summary(session, group=group)
    await edit_card(
        bot,
        callback,
        texts.lines(
            texts.join(
                "🗑 ", texts.code("#" + str(operation.id)), " на ",
                texts.money(operation.amount), " удалена",
            ),
            texts.join("💼 В фонде: ", texts.bold(texts.money(data.fund_left))),
        ),
        keyboards.back_home_kb() if is_private(callback) else None,
    )
    await callback.answer("Удалено")
