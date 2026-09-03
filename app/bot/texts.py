"""Рендеринг сообщений бота (HTML parse mode)."""
from __future__ import annotations

from html import escape

from app.config import settings
from app.core import categories as cat
from app.core import periods
from app.core.money import format_money
from app.core.service import GroupSummary
from app.db.models import Group, Operation

SYMBOLS = {"RUB": "₽", "USD": "$", "EUR": "€", "KZT": "₸", "BYN": "Br", "UAH": "₴"}
SYMBOL = SYMBOLS.get(settings.currency.upper(), settings.currency)


def esc(value: str | None) -> str:
    return escape(value or "", quote=False)


def money(cents: int) -> str:
    return format_money(cents, SYMBOL)


def signed(cents: int) -> str:
    return ("+" if cents > 0 else "") + money(cents)


def operation_line(operation: Operation) -> str:
    """Одна строка для списков операций."""
    when = periods.format_date(operation.occurred_at)
    who = esc(operation.author.short_name)
    if operation.is_purchase:
        category = cat.get(operation.category)
        return (
            f"<code>#{operation.id}</code> {category.emoji} <b>{money(operation.amount)}</b> — "
            f"{esc(operation.title or category.title)}\n"
            f"      <i>{category.title} · {who} · {when}</i>"
        )
    return (
        f"<code>#{operation.id}</code> 💰 <b>{money(operation.amount)}</b> — взнос в фонд\n"
        f"      <i>{who} · {when}</i>"
    )


def operation_card(
    operation: Operation,
    *,
    group: Group | None = None,
    header: str | None = None,
    members_total: int | None = None,
) -> str:
    """Карточка операции с кнопками правки."""
    lines: list[str] = []
    where = f" · {esc(group.title)}" if group else ""

    if operation.is_purchase:
        category = cat.get(operation.category)
        lines.append(header or f"{category.emoji} <b>Покупка записана</b>{where}")
        lines.append(f"<b>{money(operation.amount)}</b> — {esc(operation.title or category.title)}")

        parts = len(operation.shares)
        if parts:
            per_person = operation.shares[0].amount
            names = ", ".join(esc(share.user.short_name) for share in operation.shares)
            if members_total is not None and parts < members_total:
                lines.append(f"Делится на {parts}: {names} — по {money(per_person)}")
            else:
                lines.append(f"Делится на {parts} — по {money(per_person)}")

        source = {"llm": "категория определена ИИ", "manual": "категория задана вручную"}
        hint = source.get(operation.category_source or "", "категория по ключевым словам")
        lines.append(f"<i>{category.title} · {hint}</i>")
    else:
        lines.append(header or f"💰 <b>Взнос в фонд</b>{where}")
        lines.append(f"<b>{money(operation.amount)}</b> · {esc(operation.author.short_name)}")
        if operation.title:
            lines.append(f"<i>{esc(operation.title)}</i>")

    lines.append(
        f"<i>{esc(operation.author.short_name)} · "
        f"{periods.format_date(operation.occurred_at)} · #{operation.id}</i>"
    )
    return "\n".join(lines)


def draft_card(
    *,
    kind: str,
    amount: int,
    title: str,
    category: str,
    group_title: str,
    category_source: str = "rules",
    author_name: str = "",
) -> str:
    """Черновик из inline-режима — до подтверждения."""
    if kind == "contribution":
        head = f"💰 <b>Взнос в фонд</b> · {esc(group_title)}"
        body = f"<b>{money(amount)}</b>"
    else:
        category_obj = cat.get(category)
        head = f"{category_obj.emoji} <b>Покупка</b> · {esc(group_title)}"
        hint = "определил ИИ" if category_source == "llm" else "по ключевым словам"
        body = (
            f"<b>{money(amount)}</b> — {esc(title)}\n"
            f"<i>{category_obj.title} ({hint})</i>"
        )

    who = f"\n<i>{esc(author_name)}</i>" if author_name else ""
    return f"{head}\n{body}{who}\n\n<i>⏳ Черновик — подтвердите кнопкой ниже.</i>"


def summary_text(data: GroupSummary, *, with_header: bool = True) -> str:
    lines: list[str] = []
    if with_header:
        lines.append(f"<b>{esc(data.group.title)}</b>")
    lines.append(f"Осталось в фонде: <b>{money(data.fund_left)}</b>")
    lines.append(
        f"Внесено: {money(data.total_contributed)} · "
        f"Потрачено: {money(data.total_spent)}"
    )

    if data.members:
        lines.append("")
        lines.append("<b>Балансы</b>")
        for item in data.members:
            mark = "🟢" if item.balance > 0 else ("🔴" if item.balance < 0 else "⚪️")
            lines.append(
                f"{mark} {esc(item.user.short_name)}: <b>{signed(item.balance)}</b>"
                f"  <i>(внёс {money(item.contributed)}, доля {money(item.spent)})</i>"
            )
        debtors = [item for item in data.members if item.balance < 0]
        if debtors:
            lines.append("")
            lines.append(
                "🔴 нужно доложить в фонд: "
                + ", ".join(
                    f"{esc(i.user.short_name)} — {money(-i.balance)}" for i in debtors
                )
            )
    else:
        lines.append("\n<i>Пока нет ни одной операции.</i>")

    return "\n".join(lines)


def operations_text(operations: list[Operation], *, title: str, empty: str) -> str:
    if not operations:
        return f"<b>{title}</b>\n\n<i>{empty}</i>"
    body = "\n".join(operation_line(operation) for operation in operations)
    return f"<b>{title}</b>\n\n{body}"


def stats_caption(*, group_title: str, mode: str, period_title: str, total: int) -> str:
    what = "по категориям" if mode == "categories" else "по людям"
    return (
        f"📊 <b>Расходы {what}</b> · {esc(group_title)}\n"
        f"{period_title}, всего {money(total)}"
    )


def help_text(bot_username: str | None = None) -> str:
    mention = f"@{bot_username}" if bot_username else "@бот"
    web_line = (
        f"\n🌐 <b>Веб-версия</b>: /web в личке — одноразовая ссылка на мини-аппу, она же открывается "
        f"в обычном браузере.\n"
        if settings.web_enabled
        else ""
    )
    return (
        "<b>Общий бюджет: как пользоваться</b>\n\n"
        "Все скидываются в общий фонд, покупки списываются из него. "
        "У каждого свой баланс: сколько внёс минус его доля расходов.\n\n"
        "<b>В личке</b> — кнопки меню: внести, записать покупку, статистика, "
        "правка своих операций.\n\n"
        "<b>В группе</b>\n"
        "/add 5000 — взнос в фонд\n"
        "/buy молоко хлеб 850 — покупка (категорию определит ИИ)\n"
        "/balance — балансы участников\n"
        "/stats категории | /stats люди — круговая диаграмма\n"
        "/ops — последние операции\n"
        "/join — присоединиться к бюджету этого чата\n"
        "Можно и без команд: «внёс 5000», «купил молоко 850».\n\n"
        "<b>Инлайн — в любом чате</b>\n"
        f"<code>{mention} 850 молоко хлеб</code> — покупка\n"
        f"<code>{mention} внёс 5000</code> — взнос\n"
        f"<code>{mention} стата категории</code> — диаграмма по категориям\n"
        f"<code>{mention} стата люди</code> — диаграмма по людям\n"
        f"<code>{mention} баланс</code> — текущие балансы\n"
        f"{web_line}"
        "\nЛюбую свою операцию можно поправить или удалить кнопками на её карточке "
        "или через /ops."
    )
