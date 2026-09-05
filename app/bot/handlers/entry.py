"""Внесение денег в фонд и запись покупок."""
from __future__ import annotations

import re

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import CallbackQuery, ForceReply, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards, texts
from app.bot.callbacks import MenuCB, PayCB
from app.bot.common import (
    GROUP_CHATS,
    NO_GROUP_HINT,
    answer_rich,
    drop_prompt,
    edit_card,
    group_for_callback,
    resolve_group,
)
from app.bot.filters import PromptReply
from app.bot.states import AddContribution, AddPurchase
from app.core import service
from app.core.classifier import parse_purchase
from app.core.money import parse_amount
from app.db.models import Group, User

router = Router(name="entry")

CONTRIBUTION_RE = re.compile(
    r"^\s*(?:внёс|внес|внесла|вношу|закинул\w*|скинул\w*|пополн\w*|\+)\s*(?P<rest>.+)$",
    re.IGNORECASE,
)
PURCHASE_RE = re.compile(
    r"^\s*(?:купил\w*|покупка|потратил\w*|оплатил\w*|заплатил\w*|-)\s*(?P<rest>.+)$",
    re.IGNORECASE,
)


def source_for(message: Message) -> str:
    return "group" if message.chat.type in GROUP_CHATS else "dm"


def card_group(message: Message, group: Group) -> Group | None:
    """В самом чате бюджета его название подписывать не нужно — и так ясно."""
    return None if message.chat.type in GROUP_CHATS else group


async def ask_for_input(
    message: Message,
    state: FSMContext,
    next_state: State,
    *,
    text: str,
    placeholder: str,
) -> None:
    """Задаёт вопрос и запоминает своё сообщение.

    В группе спрашиваем через ForceReply ответом на команду: при включённом
    privacy mode бот видит только команды и ответы на свои сообщения, поэтому
    обычную реплику в чате он бы просто не получил.
    """
    if message.chat.type == "private":
        prompt = await answer_rich(message, 
            texts.join(text), reply_markup=keyboards.cancel_kb()
        )
    else:
        prompt = await answer_rich(message, reply=True, markdown=
            texts.lines(
                texts.join(text),
                texts.join(""),
                texts.italic("Ответьте на это сообщение — или пришлите всё одной строкой"),
                texts.code(placeholder),
            ),
            reply_markup=ForceReply(selective=True, input_field_placeholder=placeholder),
        )

    await state.set_state(next_state)
    await state.set_data({"prompt_id": prompt.message_id, "prompt_chat_id": prompt.chat.id})


async def ask_payee(
    message: Message, session: AsyncSession, user: User, group: Group, amount: int
) -> None:
    """В режиме дележа деньги отдают не в кассу, а конкретному человеку."""
    members = await service.group_members(session, group.id)
    if len(members) < 2:
        await answer_rich(
            message,
            texts.join(
                "Пока вы в бюджете один — возвращать долг некому. Позовите остальных: ",
                texts.cmd("/join"),
            ),
        )
        return

    await answer_rich(
        message,
        texts.blocks(
            texts.heading(3, texts.join("💸 Возврат долга — ",
                                        texts.bold(texts.money(amount)))),
            texts.join("Кому вы отдали деньги?"),
        ),
        reply_markup=keyboards.payees_kb(members, amount=amount, exclude_id=user.id),
    )


async def record_contribution(
    message: Message,
    session: AsyncSession,
    user: User,
    group: Group,
    amount: int,
    source: str,
) -> None:
    if group.is_split:
        await ask_payee(message, session, user, group, amount)
        return

    operation = await service.add_contribution(
        session, group_id=group.id, author_id=user.id, amount=amount, source=source
    )
    data = await service.summary(session, group=group)
    await answer_rich(
        message,
        texts.operation_card(
            operation, group=card_group(message, group), data=data
        ),
        reply_markup=keyboards.operation_kb(operation, compact=True),
        gif="topup",
    )


@router.callback_query(PayCB.filter())
async def pay_debt(
    callback: CallbackQuery,
    callback_data: PayCB,
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    """Выбран получатель возврата — записываем перевод."""
    group = await group_for_callback(session, callback, user)
    if group is None:
        await callback.answer("Сначала выберите бюджет", show_alert=True)
        return

    try:
        operation = await service.add_transfer(
            session,
            group_id=group.id,
            author_id=user.id,
            to_user_id=callback_data.to_id,
            amount=callback_data.amount,
            source="dm" if callback.message.chat.type == "private" else "group",
        )
    except service.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    data = await service.summary(session, group=group)
    await callback.answer("Записал")
    await edit_card(
        bot,
        callback,
        texts.operation_card(operation, data=data),
        keyboards.operation_kb(operation, compact=True),
        gif="topup",
    )


async def record_purchase(
    message: Message,
    session: AsyncSession,
    user: User,
    group: Group,
    text: str,
    source: str,
    bot: Bot,
) -> None:
    await bot.send_chat_action(message.chat.id, "typing")
    parsed = await parse_purchase(text)
    if not parsed.amount:
        await answer_rich(message, 
            texts.join("Не нашёл сумму. Напишите так: ", texts.code("молоко хлеб 850")),
            reply_markup=keyboards.cancel_kb() if message.chat.type == "private" else None,
        )
        return

    operation = await service.add_purchase(
        session,
        group_id=group.id,
        author_id=user.id,
        amount=parsed.amount,
        category=parsed.category,
        title=parsed.title,
        source=source,
        category_source=parsed.source,
        raw_text=text,
    )
    members = await service.group_members(session, group.id)
    data = await service.summary(session, group=group)
    await answer_rich(
        message,
        texts.operation_card(
            operation,
            group=card_group(message, group),
            members_total=len(members),
            data=data,
        ),
        reply_markup=keyboards.operation_kb(operation, compact=True),
        gif="buy",
    )


# --------------------------------------------------------------------------- #
#  Команды: работают и в личке, и в группе
# --------------------------------------------------------------------------- #


@router.message(Command("add"))
async def add_command(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    group = await resolve_group(
        session, message, user, join=message.chat.type in GROUP_CHATS
    )
    if group is None:
        await answer_rich(message, NO_GROUP_HINT)
        return

    amount, _ = parse_amount(command.args or "")
    if amount is None:
        await ask_for_input(
            message,
            state,
            AddContribution.amount,
            text="💰 Сколько внести в общий фонд?",
            placeholder="/add 5000",
        )
        return
    await record_contribution(message, session, user, group, amount, source_for(message))


@router.message(Command("buy"))
async def buy_command(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    bot: Bot,
) -> None:
    group = await resolve_group(
        session, message, user, join=message.chat.type in GROUP_CHATS
    )
    if group is None:
        await answer_rich(message, NO_GROUP_HINT)
        return

    text = (command.args or "").strip()
    if not text:
        await ask_for_input(
            message,
            state,
            AddPurchase.text,
            text="🛒 Что купили и на сколько?",
            placeholder="/buy молоко хлеб 850",
        )
        return
    await record_purchase(message, session, user, group, text, source_for(message), bot)


# --------------------------------------------------------------------------- #
#  Меню в личке
# --------------------------------------------------------------------------- #


@router.callback_query(MenuCB.filter(F.action == "add"))
async def menu_add(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.set_state(AddContribution.amount)
    await state.set_data({})
    await edit_card(
        bot,
        callback,
        texts.lines(
            texts.join("💰 ", texts.bold("Сколько внести в общий фонд?")),
            texts.join("Напишите сумму, например ", texts.code("5000")),
        ),
        keyboards.cancel_kb(),
    )
    await callback.answer()


@router.callback_query(MenuCB.filter(F.action == "buy"))
async def menu_buy(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.set_state(AddPurchase.text)
    await state.set_data({})
    await edit_card(
        bot,
        callback,
        texts.lines(
            texts.join("🛒 ", texts.bold("Что купили и на сколько?")),
            texts.join("Например: ", texts.code("молоко хлеб яйца 850")),
            texts.join("или ", texts.code("чайник bosch 3500")),
            texts.join(""),
            texts.italic("Категорию определит ИИ — потом её можно поправить кнопкой"),
        ),
        keyboards.cancel_kb(),
    )
    await callback.answer()


@router.message(AddContribution.amount, PromptReply(), F.text)
async def contribution_amount(
    message: Message, session: AsyncSession, user: User, state: FSMContext, bot: Bot
) -> None:
    amount, _ = parse_amount(message.text)
    if amount is None:
        await answer_rich(message, reply=True, markdown=
            texts.join("Не понял сумму. Напишите числом, например ", texts.code("5000")),
            reply_markup=keyboards.cancel_kb() if message.chat.type == "private" else None,
        )
        return

    data = await state.get_data()
    await state.clear()
    await drop_prompt(bot, data)

    # В группе деньги идут в бюджет этого чата, а не в активный бюджет человека.
    group = await resolve_group(session, message, user)
    if group is None:
        await answer_rich(message, NO_GROUP_HINT)
        return

    await record_contribution(message, session, user, group, amount, source_for(message))


@router.message(AddPurchase.text, PromptReply(), F.text)
async def purchase_text(
    message: Message, session: AsyncSession, user: User, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    await state.clear()
    await drop_prompt(bot, data)

    group = await resolve_group(session, message, user)
    if group is None:
        await answer_rich(message, NO_GROUP_HINT)
        return

    await record_purchase(
        message, session, user, group, message.text, source_for(message), bot
    )


# --------------------------------------------------------------------------- #
#  Свободный текст: «внёс 5000», «купил молоко 850»
# --------------------------------------------------------------------------- #


@router.message(F.text.regexp(CONTRIBUTION_RE))
async def text_contribution(message: Message, session: AsyncSession, user: User) -> None:
    match = CONTRIBUTION_RE.match(message.text)
    amount, _ = parse_amount(match.group("rest"))
    if amount is None:
        return

    group = await resolve_group(
        session, message, user, join=message.chat.type in GROUP_CHATS
    )
    if group is None:
        await answer_rich(message, NO_GROUP_HINT)
        return
    await record_contribution(message, session, user, group, amount, source_for(message))


@router.message(F.text.regexp(PURCHASE_RE))
async def text_purchase(
    message: Message, session: AsyncSession, user: User, bot: Bot
) -> None:
    match = PURCHASE_RE.match(message.text)
    rest = match.group("rest")
    if parse_amount(rest)[0] is None:
        return

    group = await resolve_group(
        session, message, user, join=message.chat.type in GROUP_CHATS
    )
    if group is None:
        await answer_rich(message, NO_GROUP_HINT)
        return
    await record_purchase(message, session, user, group, rest, source_for(message), bot)
