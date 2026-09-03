"""Стартовое меню, помощь, выбор группы, вход в веб."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards, texts
from app.bot.callbacks import GroupCB, MenuCB
from app.bot.common import (
    GROUP_CHATS,
    NO_GROUP_HINT,
    edit_card,
    is_private,
    resolve_group,
    web_app_url,
)
from app.config import settings
from app.core import service
from app.db.models import User

router = Router(name="menu")


async def render_home(
    session: AsyncSession, user: User, *, private: bool = True
) -> tuple[str, object]:
    """Главный экран. Вне лички кнопка web_app недопустима — Telegram отвергает
    такую клавиатуру целиком (BUTTON_TYPE_INVALID), и сообщение не отправляется."""
    group = await service.resolve_active_group(session, user)
    if group is None:
        return NO_GROUP_HINT, keyboards.back_home_kb()

    data = await service.summary(session, group=group)
    groups = await service.user_groups(session, user.id)
    hint = "\n\n<i>Сменить группу: /groups</i>" if len(groups) > 1 else ""
    text = f"👋 <b>{texts.esc(user.short_name)}</b>\n\n{texts.summary_text(data)}{hint}"
    return text, keyboards.main_menu(web_app_url=web_app_url() if private else None)


@router.message(CommandStart(), F.chat.type.in_(GROUP_CHATS))
@router.message(Command("join"), F.chat.type.in_(GROUP_CHATS))
async def join_group_chat(message: Message, session: AsyncSession, user: User) -> None:
    existing = await service.get_or_create_group_for_chat(
        session, tg_chat_id=message.chat.id, title=message.chat.title or "Общий бюджет"
    )
    was_member = await service.is_member(
        session, group_id=existing.id, user_id=user.id
    )

    group = await resolve_group(session, message, user, join=True)
    members = await service.group_members(session, group.id)
    names = ", ".join(texts.esc(m.short_name) for m in members)

    if was_member:
        await message.answer(
            f"Вы уже участвуете в бюджете «{texts.esc(group.title)}».\n"
            f"Участники ({len(members)}): {names}"
        )
        return

    head = (
        f"✅ Бюджет «{texts.esc(group.title)}» подключён к этому чату."
        if len(members) == 1
        else f"✅ {texts.esc(user.short_name)} в деле — бюджет «{texts.esc(group.title)}»."
    )
    await message.answer(
        f"{head}\n"
        f"Участники ({len(members)}): {names}\n\n"
        "Остальные — отправьте /join, чтобы попасть в расчёты.\n"
        "Дальше: /add 5000 — взнос, /buy молоко 850 — покупка, /help — всё остальное."
    )


@router.message(CommandStart())
async def start_private(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    await state.clear()
    if (command.args or "").strip() == "web":
        await send_login_link(message, session, user)
        return
    text, markup = await render_home(session, user)
    await message.answer(text, reply_markup=markup)


@router.message(Command("help"))
async def help_command(message: Message, bot: Bot) -> None:
    me = await bot.me()
    await message.answer(texts.help_text(me.username))


@router.message(Command("newgroup"), F.chat.type == "private")
async def new_group(
    message: Message, command: CommandObject, session: AsyncSession, user: User
) -> None:
    title = (command.args or "").strip()
    if not title:
        await message.answer("Укажите название: <code>/newgroup Квартира на Лесной</code>")
        return
    group = await service.create_group(session, title=title, owner=user)
    await service.set_active_group(session, user, group.id)
    await message.answer(
        f"✅ Бюджет «{texts.esc(group.title)}» создан и выбран активным.\n\n"
        "Чтобы подключить остальных, добавьте бота в общий чат и отправьте там /join — "
        "или пусть каждый напишет боту /groups после приглашения."
    )


@router.message(Command("groups"), F.chat.type == "private")
async def groups_command(message: Message, session: AsyncSession, user: User) -> None:
    groups = await service.user_groups(session, user.id)
    if not groups:
        await message.answer(NO_GROUP_HINT)
        return
    await message.answer(
        "Ваши бюджеты — выберите активный (в него пойдут операции из лички и inline):",
        reply_markup=keyboards.groups_kb(groups, user.active_group_id),
    )


@router.callback_query(GroupCB.filter(F.action == "pick"))
async def pick_group(
    callback: CallbackQuery,
    callback_data: GroupCB,
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    try:
        group = await service.set_active_group(session, user, callback_data.group_id)
    except service.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(f"Активная группа: {group.title}")
    text, markup = await render_home(session, user)
    await edit_card(bot, callback, text, markup)


@router.message(Command("members"))
async def members_command(message: Message, session: AsyncSession, user: User) -> None:
    group = await resolve_group(session, message, user)
    if group is None:
        await message.answer(NO_GROUP_HINT)
        return
    members = await service.group_members(session, group.id)
    lines = "\n".join(f"• {texts.esc(m.display_name)}" for m in members) or "<i>пусто</i>"
    await message.answer(f"<b>{texts.esc(group.title)}</b>\nУчастники:\n{lines}")


@router.message(Command("leave"), F.chat.type.in_(GROUP_CHATS))
async def leave_command(message: Message, session: AsyncSession, user: User) -> None:
    group = await resolve_group(session, message, user)
    if group is None:
        return
    await service.leave_group(session, group_id=group.id, user_id=user.id)
    await message.answer(
        f"{texts.esc(user.short_name)} больше не участвует в расчётах этого бюджета. "
        "Прошлые операции остались в истории."
    )


async def send_login_link(message: Message, session: AsyncSession, user: User) -> None:
    """Выдаёт одноразовую ссылку входа. Только в личке — см. web_command_in_group."""
    if not settings.web_enabled:
        await message.answer(
            "Веб-версия не настроена: в .env нужен PUBLIC_BASE_URL с https."
        )
        return

    token = await service.create_login_token(session, user.id)
    await message.answer(
        "🌐 Ссылка для входа в веб-версию — одноразовая, действует 15 минут:\n"
        f"{settings.public_base}/?login={token}\n\n"
        "Открывается в любом браузере и как мини-аппа в Telegram.\n"
        "<i>Никому её не пересылайте: она пускает в ваш аккаунт без пароля.</i>",
        disable_web_page_preview=True,
    )


@router.message(Command("web"), F.chat.type == "private")
async def web_command(message: Message, session: AsyncSession, user: User) -> None:
    await send_login_link(message, session, user)


@router.message(Command("web"))
async def web_command_in_group(message: Message, bot: Bot) -> None:
    """В общем чате ссылку не выдаём и токен не создаём.

    Ссылка пускает в аккаунт того, кто набрал команду, без пароля — в группе
    по ней вошёл бы любой, кто нажмёт первым.
    """
    me = await bot.me()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть бота", url=f"https://t.me/{me.username}?start=web"
                )
            ]
        ]
    )
    await message.reply(
        "🔒 Ссылку для входа выдаю только в личных сообщениях: в общем чате "
        "по ней мог бы зайти в ваш аккаунт кто угодно.",
        reply_markup=keyboard,
    )


@router.callback_query(MenuCB.filter(F.action == "home"))
async def go_home(
    callback: CallbackQuery, session: AsyncSession, user: User, state: FSMContext, bot: Bot
) -> None:
    await state.clear()
    text, markup = await render_home(session, user, private=is_private(callback))
    await edit_card(bot, callback, text, markup)
    await callback.answer()


@router.callback_query(MenuCB.filter(F.action == "cancel"))
async def cancel_action(
    callback: CallbackQuery, session: AsyncSession, user: User, state: FSMContext, bot: Bot
) -> None:
    """Отмена ввода: в личке возвращаемся в меню, в группе убираем сообщение."""
    await state.clear()

    if not is_private(callback):
        try:
            if callback.message:
                await callback.message.delete()
        except TelegramBadRequest:
            # Нет прав на удаление или сообщение старше 48 часов — гасим текстом.
            await edit_card(bot, callback, "✖️ Отменено.", None)
        await callback.answer("Отменено")
        return

    text, markup = await render_home(session, user, private=True)
    await edit_card(bot, callback, text, markup)
    await callback.answer("Отменено")


@router.callback_query(MenuCB.filter(F.action == "close"))
async def close_message(callback: CallbackQuery) -> None:
    """Убирает сообщение бота — например, диаграмму из общего чата."""
    try:
        if callback.message:
            await callback.message.delete()
    except TelegramBadRequest:
        await callback.answer(
            "Не получилось удалить: сообщение старше 48 часов", show_alert=True
        )
        return
    await callback.answer()


@router.callback_query(MenuCB.filter(F.action == "help"))
async def menu_help(callback: CallbackQuery, bot: Bot) -> None:
    me = await bot.me()
    await edit_card(bot, callback, texts.help_text(me.username), keyboards.back_home_kb())
    await callback.answer()


@router.callback_query(MenuCB.filter(F.action == "group"))
async def menu_group(
    callback: CallbackQuery, session: AsyncSession, user: User, bot: Bot
) -> None:
    groups = await service.user_groups(session, user.id)
    if not groups:
        await edit_card(bot, callback, NO_GROUP_HINT, keyboards.back_home_kb())
        await callback.answer()
        return

    group = await service.resolve_active_group(session, user)
    members = await service.group_members(session, group.id) if group else []
    names = "\n".join(f"• {texts.esc(m.display_name)}" for m in members) or "<i>пусто</i>"
    text = (
        f"<b>{texts.esc(group.title if group else '')}</b>\n"
        f"Участники:\n{names}\n\n"
        "Выберите активную группу — в неё пойдут операции из лички и inline:"
    )
    await edit_card(bot, callback, text, keyboards.groups_kb(groups, user.active_group_id))
    await callback.answer()
