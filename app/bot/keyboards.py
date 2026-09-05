"""Инлайн-клавиатуры бота."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core import categories as cat
from app.core import periods
from app.db.models import Group, Operation, User

from app.bot.callbacks import (
    DraftCB,
    GroupCB,
    MenuCB,
    ModeCB,
    OpCB,
    OpsPageCB,
    PayCB,
    StatsCB,
)


def main_menu(
    *, web_app_url: str | None = None, split: bool = False
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💸 Вернуть долг" if split else "💰 Внести в фонд",
        callback_data=MenuCB(action="add"),
    )
    builder.button(text="🛒 Записать покупку", callback_data=MenuCB(action="buy"))
    builder.button(text="📊 Статистика", callback_data=MenuCB(action="stats"))
    builder.button(text="📒 Операции", callback_data=MenuCB(action="ops"))
    builder.button(text="👥 Группа", callback_data=MenuCB(action="group"))
    builder.button(text="❓ Помощь", callback_data=MenuCB(action="help"))
    if web_app_url:
        builder.button(text="🌐 Открыть приложение", web_app=WebAppInfo(url=web_app_url))
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def mode_kb(group_id: int) -> InlineKeyboardMarkup:
    """Выбор режима: как считать деньги в этом бюджете."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🏦 Общая касса", callback_data=ModeCB(group_id=group_id, mode="fund")
    )
    builder.button(
        text="🧮 Делим расходы", callback_data=ModeCB(group_id=group_id, mode="split")
    )
    builder.adjust(1)
    return builder.as_markup()


def payees_kb(members: list[User], *, amount: int, exclude_id: int) -> InlineKeyboardMarkup:
    """Кому вернуть долг. Себя в списке нет — перевод себе ничего не меняет."""
    builder = InlineKeyboardBuilder()
    for member in members:
        if member.id == exclude_id:
            continue
        builder.button(
            text=member.short_name,
            callback_data=PayCB(to_id=member.id, amount=amount),
        )
    builder.button(text="✖️ Отмена", callback_data=MenuCB(action="cancel"))
    builder.adjust(2)
    return builder.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✖️ Отмена", callback_data=MenuCB(action="cancel").pack())]
        ]
    )


def back_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В меню", callback_data=MenuCB(action="home").pack())]
        ]
    )


def no_group_kb() -> InlineKeyboardMarkup:
    """Экран без бюджета: дальше идти некуда, пока его не создали."""
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Создать бюджет", callback_data=MenuCB(action="newgroup"))
    builder.button(text="❓ Как это работает", callback_data=MenuCB(action="help"))
    builder.adjust(1)
    return builder.as_markup()


def draft_kb(draft_id: str, *, with_category: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Записать", callback_data=DraftCB(action="ok", draft_id=draft_id))
    builder.button(text="✖️ Отмена", callback_data=DraftCB(action="cancel", draft_id=draft_id))
    if with_category:
        builder.button(
            text="✏️ Категория",
            callback_data=DraftCB(action="cat", draft_id=draft_id),
            style="primary",
        )
    builder.adjust(2, 1)
    return builder.as_markup()


def draft_categories_kb(draft_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in cat.CATEGORIES:
        builder.button(
            text=f"{category.emoji} {category.title}",
            callback_data=DraftCB(action="setcat", draft_id=draft_id, value=category.code),
        )
    builder.button(text="⬅️ Назад", callback_data=DraftCB(action="back", draft_id=draft_id))
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def operation_kb(
    operation: Operation, *, compact: bool = False, private: bool = True
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if operation.is_purchase:
        builder.button(
            text="✏️ Категория",
            callback_data=OpCB(action="cat", op_id=operation.id),
            style="primary",
        )
        builder.button(
            text="👥 Участники",
            callback_data=OpCB(action="parts", op_id=operation.id),
            style="primary",
        )
    builder.button(
        text="✏️ Сумма",
        callback_data=OpCB(action="amount", op_id=operation.id),
        style="primary",
    )
    builder.button(
        text="🗑 Удалить",
        callback_data=OpCB(action="del", op_id=operation.id),
        style="danger",
    )
    if not compact and private:
        builder.button(text="⬅️ В меню", callback_data=MenuCB(action="home"))
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def op_categories_kb(op_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in cat.CATEGORIES:
        builder.button(
            text=f"{category.emoji} {category.title}",
            callback_data=OpCB(action="setcat", op_id=op_id, value=category.code),
        )
    builder.button(text="⬅️ Назад", callback_data=OpCB(action="card", op_id=op_id))
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def op_participants_kb(operation: Operation, members: list[User]) -> InlineKeyboardMarkup:
    chosen = {share.user_id for share in operation.shares}
    builder = InlineKeyboardBuilder()
    for member in members:
        mark = "✅" if member.id in chosen else "☐"
        builder.button(
            text=f"{mark} {member.short_name}",
            callback_data=OpCB(action="toggle", op_id=operation.id, value=str(member.id)),
        )
    builder.button(text="⬅️ Готово", callback_data=OpCB(action="card", op_id=operation.id))
    builder.adjust(2)
    return builder.as_markup()


def confirm_delete_kb(op_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🗑 Да, удалить",
        callback_data=OpCB(action="delyes", op_id=op_id),
        style="danger",
    )
    builder.button(text="⬅️ Отмена", callback_data=OpCB(action="card", op_id=op_id))
    builder.adjust(2)
    return builder.as_markup()


def stats_kb(mode: str, period: str, *, private: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code, title in (("categories", "По категориям"), ("people", "По людям")):
        builder.button(
            text=("• " if code == mode else "") + title,
            callback_data=StatsCB(mode=code, period=period),
        )
    for code, title in periods.PERIODS.items():
        builder.button(
            text=("• " if code == period else "") + title,
            callback_data=StatsCB(mode=mode, period=code),
        )
    # Личное меню посреди общего чата не нужно — там уместнее убрать диаграмму.
    if private:
        builder.button(text="⬅️ В меню", callback_data=MenuCB(action="home"))
    else:
        builder.button(
            text="🗑 Удалить", callback_data=MenuCB(action="close"), style="danger"
        )
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def groups_kb(groups: list[Group], active_id: int | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for group in groups:
        mark = "• " if group.id == active_id else ""
        builder.button(
            text=f"{mark}{group.title}",
            callback_data=GroupCB(action="pick", group_id=group.id),
        )
    builder.button(text="⬅️ В меню", callback_data=MenuCB(action="home"))
    builder.adjust(1)
    return builder.as_markup()


def ops_kb(
    operations: list[Operation],
    *,
    scope: str,
    offset: int,
    has_more: bool,
    page: int = 10,
) -> InlineKeyboardMarkup:
    """Номера операций (переход к карточке) + переключатель списка и листание."""
    builder = InlineKeyboardBuilder()
    for operation in operations:
        builder.button(
            text=f"#{operation.id}", callback_data=OpCB(action="card", op_id=operation.id)
        )
    numbers = len(operations)

    other = "all" if scope == "mine" else "mine"
    builder.button(
        text="👤 Только мои" if other == "mine" else "👥 Все операции",
        callback_data=OpsPageCB(scope=other, offset=0),
    )
    nav = 0
    if offset:
        builder.button(
            text="⬅️ Новее",
            callback_data=OpsPageCB(scope=scope, offset=max(0, offset - page)),
        )
        nav += 1
    if has_more:
        builder.button(
            text="Старее ➡️", callback_data=OpsPageCB(scope=scope, offset=offset + page)
        )
        nav += 1
    builder.button(text="⬅️ В меню", callback_data=MenuCB(action="home"))

    rows = [5] * (numbers // 5)
    if numbers % 5:
        rows.append(numbers % 5)
    rows.append(1)
    if nav:
        rows.append(nav)
    rows.append(1)
    builder.adjust(*rows)
    return builder.as_markup()
