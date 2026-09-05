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
from app.bot.callbacks import GroupCB, MenuCB, ModeCB
from app.bot.states import NewGroup
from app.bot.common import (
    GROUP_CHATS,
    NO_GROUP_HINT,
    answer_rich,
    edit_card,
    is_private,
    resolve_group,
    web_app_url,
)
from app.config import settings
from app.core import service
from app.db.models import Group, User

router = Router(name="menu")


async def render_home(
    session: AsyncSession, user: User, *, private: bool = True
) -> tuple[str, object]:
    """Главный экран. Вне лички кнопка web_app недопустима — Telegram отвергает
    такую клавиатуру целиком (BUTTON_TYPE_INVALID), и сообщение не отправляется."""
    group = await service.resolve_active_group(session, user)
    if group is None:
        return NO_GROUP_HINT, keyboards.no_group_kb()

    data = await service.summary(session, group=group)
    groups = await service.user_groups(session, user.id)
    text = texts.lines(
        texts.join("👋 ", texts.bold(user.short_name)),
        texts.join(""),
        # Кто сколько должен — в /balance: в меню это лишний блок
        texts.summary_text(data, with_debts=False),
    )
    if len(groups) > 1:
        text = texts.blocks(text, texts.italic("Сменить бюджет: /groups"))
    return text, keyboards.main_menu(
        web_app_url=web_app_url() if private else None, split=group.is_split
    )


@router.message(CommandStart(), F.chat.type.in_(GROUP_CHATS))
@router.message(Command("join"), F.chat.type.in_(GROUP_CHATS))
async def join_group_chat(message: Message, session: AsyncSession, user: User) -> None:
    existing, fresh = await service.get_or_create_group_for_chat(
        session, tg_chat_id=message.chat.id, title=message.chat.title or "Общий бюджет"
    )
    was_member = await service.is_member(
        session, group_id=existing.id, user_id=user.id
    )

    group = await resolve_group(session, message, user, join=True)
    members = await service.group_members(session, group.id)
    names = ", ".join(m.short_name for m in members)

    if was_member:
        await answer_rich(message, 
            texts.lines(
                texts.join("Вы уже участвуете в бюджете «", group.title, "»."),
                texts.join("👥 Участники (", str(len(members)), "): ", names),
            )
        )
        return

    if len(members) == 1:
        head = texts.join("✅ Бюджет «", group.title, "» подключён к этому чату")
    else:
        head = texts.join("✅ ", user.short_name, " в деле — «", group.title, "»")

    await answer_rich(
        message,
        texts.blocks(
            texts.heading(2, head),
            texts.join("👥 Участники (", str(len(members)), "): ", names),
            texts.bold("Дальше"),
            texts.bullets(
                texts.join(
                    texts.cmd("/add 5000"),
                    " — вернуть долг" if group.is_split else " — взнос в фонд",
                ),
                texts.join(texts.cmd("/buy молоко хлеб 850"), " — покупка"),
                texts.join(texts.cmd("/join"), " — остальным, чтобы попасть в расчёты"),
                texts.join(texts.cmd("/help"), " — всё остальное"),
            ),
        ),
    )

    # Бюджет чата только что появился — сразу спрашиваем, как считать деньги.
    if fresh:
        await answer_rich(
            message, texts.mode_prompt(group), reply_markup=keyboards.mode_kb(group.id)
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
    await answer_rich(message, text, reply_markup=markup)


@router.message(Command("help"))
async def help_command(
    message: Message, bot: Bot, session: AsyncSession, user: User
) -> None:
    me = await bot.me()
    group = await resolve_group(session, message, user)
    await answer_rich(
        message, texts.help_text(me.username, split=bool(group and group.is_split))
    )


@router.message(Command("newgroup"), F.chat.type == "private")
async def new_group(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    title = (command.args or "").strip()
    if not title:
        # Без аргумента спрашиваем название, а не показываем синтаксис команды.
        await state.set_state(NewGroup.title)
        await state.set_data({})
        await answer_rich(
            message,
            texts.blocks(
                texts.heading(2, "➕ Новый бюджет"),
                texts.join("Как его назвать? Напишите название сообщением."),
                texts.italic("Например: Квартира на Лесной"),
            ),
            reply_markup=keyboards.cancel_kb(),
        )
        return

    group = await service.create_group(session, title=title, owner=user)
    await service.set_active_group(session, user, group.id)
    await answer_rich(
        message, texts.mode_prompt(group), reply_markup=keyboards.mode_kb(group.id)
    )


@router.message(Command("groups"), F.chat.type == "private")
async def groups_command(message: Message, session: AsyncSession, user: User) -> None:
    groups = await service.user_groups(session, user.id)
    if not groups:
        await answer_rich(message, NO_GROUP_HINT)
        return
    await answer_rich(message, 
        texts.join("Ваши бюджеты — выберите активный (операции из лички и inline пойдут в него):"),
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


MODE_ALIASES = {
    "касса": "fund", "фонд": "fund", "fund": "fund", "общая": "fund",
    "делим": "split", "сплит": "split", "split": "split", "splitwise": "split",
}


@router.callback_query(ModeCB.filter())
async def pick_mode(
    callback: CallbackQuery,
    callback_data: ModeCB,
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    group = await session.get(Group, callback_data.group_id)
    if group is None:
        await callback.answer("Бюджет не найден", show_alert=True)
        return
    if not await service.can_set_mode(session, group=group, user_id=user.id):
        await callback.answer("Режим меняет админ бюджета", show_alert=True)
        return

    try:
        await service.set_group_mode(session, group=group, mode=callback_data.mode)
    except service.ServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer(texts.MODE_TITLES[group.mode])
    await edit_card(bot, callback, texts.mode_card(group), None)

    # В личке сразу возвращаем человека в меню: выбор режима — не финал.
    if is_private(callback):
        text, markup = await render_home(session, user)
        await answer_rich(callback.message, text, reply_markup=markup)


@router.message(Command("mode"))
async def mode_command(
    message: Message, command: CommandObject, session: AsyncSession, user: User
) -> None:
    group = await resolve_group(session, message, user)
    if group is None:
        await answer_rich(message, NO_GROUP_HINT)
        return

    wanted = MODE_ALIASES.get((command.args or "").strip().lower())
    if wanted is None:
        # Без аргумента показываем текущий режим и кнопки для смены.
        await answer_rich(
            message, texts.mode_card(group), reply_markup=keyboards.mode_kb(group.id)
        )
        return

    if not await service.can_set_mode(session, group=group, user_id=user.id):
        await answer_rich(message, texts.join("⚠️ Режим меняет админ бюджета"))
        return
    try:
        await service.set_group_mode(session, group=group, mode=wanted)
    except service.ServiceError as exc:
        await answer_rich(message, texts.join("⚠️ ", str(exc)))
        return
    await answer_rich(message, texts.mode_card(group))


@router.message(Command("members"))
async def members_command(message: Message, session: AsyncSession, user: User) -> None:
    group = await resolve_group(session, message, user)
    if group is None:
        await answer_rich(message, NO_GROUP_HINT)
        return
    members = await service.group_members(session, group.id)
    roster = texts.lines(*[texts.join("• ", m.display_name) for m in members]) or texts.italic("пусто")
    await answer_rich(message, 
        texts.lines(texts.join("💼 ", texts.bold(group.title)), texts.quote(roster))
    )


@router.message(Command("leave"), F.chat.type.in_(GROUP_CHATS))
async def leave_command(message: Message, session: AsyncSession, user: User) -> None:
    group = await resolve_group(session, message, user)
    if group is None:
        return
    await service.leave_group(session, group_id=group.id, user_id=user.id)
    await answer_rich(message, 
        texts.join(
            user.short_name,
            " больше не участвует в расчётах этого бюджета. ",
            "Прошлые операции остались в истории.",
        )
    )


async def send_login_link(message: Message, session: AsyncSession, user: User) -> None:
    """Выдаёт одноразовую ссылку входа. Только в личке — см. web_command_in_group."""
    if not settings.web_enabled:
        await answer_rich(message, 
            texts.join("Веб-версия не настроена: в .env нужен PUBLIC_BASE_URL с https.")
        )
        return

    token = await service.create_login_token(session, user.id)
    await answer_rich(message, 
        texts.lines(
            texts.join("🌐 ", texts.bold("Вход в веб-версию")),
            texts.quote(
                texts.lines(
                    texts.code(settings.public_base + "/?login=" + token),
                    texts.join("Одноразовая, действует 15 минут."),
                    texts.join("Открывается в браузере и как мини-аппа в Telegram."),
                )
            ),
            texts.italic("Никому её не пересылайте: она пускает в ваш аккаунт без пароля"),
        ),
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
    await answer_rich(message, reply=True, markdown=
        texts.join(
            "🔒 Ссылку для входа выдаю только в личных сообщениях: в общем чате ",
            "по ней мог бы зайти в ваш аккаунт кто угодно.",
        ),
        reply_markup=keyboard,
    )


@router.callback_query(MenuCB.filter(F.action == "home"))
async def go_home(
    callback: CallbackQuery, session: AsyncSession, user: User, state: FSMContext, bot: Bot
) -> None:
    await state.clear()
    text, markup = await render_home(session, user, private=is_private(callback))
    await edit_card(bot, callback, text, markup)

    # Без бюджета меню открывать некуда — экран остаётся прежним, поэтому
    # объясняем это словами, иначе кнопка выглядит сломанной.
    if not await service.user_groups(session, user.id):
        await callback.answer("Сначала создайте бюджет", show_alert=True)
        return
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


@router.callback_query(MenuCB.filter(F.action == "newgroup"))
async def ask_group_title(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    await state.set_state(NewGroup.title)
    await state.set_data({})
    await edit_card(
        bot,
        callback,
        texts.blocks(
            texts.heading(2, "➕ Новый бюджет"),
            texts.join("Как его назвать? Напишите название сообщением."),
            texts.italic("Например: Квартира на Лесной"),
        ),
        keyboards.cancel_kb(),
    )
    await callback.answer()


@router.message(NewGroup.title, F.chat.type == "private", F.text)
async def create_group_by_title(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    title = (message.text or "").strip()
    if not title:
        await answer_rich(message, texts.join("Название не может быть пустым."))
        return

    await state.clear()
    group = await service.create_group(session, title=title, owner=user)
    await service.set_active_group(session, user, group.id)
    await answer_rich(
        message, texts.mode_prompt(group), reply_markup=keyboards.mode_kb(group.id)
    )


@router.callback_query(MenuCB.filter(F.action == "help"))
async def menu_help(
    callback: CallbackQuery, session: AsyncSession, user: User, bot: Bot
) -> None:
    me = await bot.me()
    # Пока бюджета нет, «В меню» вести некуда — оставляем кнопку создания.
    has_groups = bool(await service.user_groups(session, user.id))
    markup = keyboards.back_home_kb() if has_groups else keyboards.no_group_kb()
    active = await service.resolve_active_group(session, user)
    await edit_card(
        bot,
        callback,
        texts.help_text(me.username, split=bool(active and active.is_split)),
        markup,
    )
    await callback.answer()


@router.callback_query(MenuCB.filter(F.action == "group"))
async def menu_group(
    callback: CallbackQuery, session: AsyncSession, user: User, bot: Bot
) -> None:
    groups = await service.user_groups(session, user.id)
    if not groups:
        await edit_card(bot, callback, NO_GROUP_HINT, keyboards.no_group_kb())
        await callback.answer()
        return

    group = await service.resolve_active_group(session, user)
    members = await service.group_members(session, group.id) if group else []
    roster = texts.lines(*[texts.join("• ", m.display_name) for m in members]) or texts.italic("пусто")
    text = texts.lines(
        texts.join("💼 ", texts.bold(group.title if group else "")),
        texts.quote(roster),
        texts.join("Выберите активный бюджет — в него пойдут операции из лички и inline:"),
    )
    await edit_card(bot, callback, text, keyboards.groups_kb(groups, user.active_group_id))
    await callback.answer()
