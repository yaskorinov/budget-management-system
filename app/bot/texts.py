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



def quote(body: str, *, expandable: bool = False) -> str:
    """Цитата Telegram: сворачиваемая — для длинных списков."""
    tag = "<blockquote expandable>" if expandable else "<blockquote>"
    return f"{tag}{body}</blockquote>"


def pre(body: str) -> str:
    """Моноширинный блок: только в нём колонки выстраиваются ровно."""
    return f"<pre>{escape(body, quote=False)}</pre>"


def operation_line(operation: Operation) -> str:
    """Две строки для списка операций."""
    when = periods.format_date(operation.occurred_at)
    who = esc(operation.author.short_name)

    if operation.is_purchase:
        category = cat.get(operation.category)
        head = (
            f"{category.emoji} <b>{money(operation.amount)}</b> · "
            f"{esc(operation.title or category.title)}"
        )
        tail = f"<i>#{operation.id} · {category.title} · {who} · {when}</i>"
    else:
        head = f"💰 <b>{money(operation.amount)}</b> · взнос в фонд"
        tail = f"<i>#{operation.id} · {who} · {when}</i>"

    return f"{head}\n{tail}"


def operation_card(
    operation: Operation,
    *,
    group: Group | None = None,
    header: str | None = None,
    members_total: int | None = None,
    fund_left: int | None = None,
) -> str:
    """Карточка операции: заголовок с суммой, подробности — в цитате."""
    facts: list[str] = []

    if operation.is_purchase:
        category = cat.get(operation.category)
        title = esc(operation.title or category.title)
        head = header or (
            f"{category.emoji} <b>{title}</b> — <b>{money(operation.amount)}</b>"
        )

        origin = {
            "llm": "определил ИИ",
            "manual": "выбрана вручную",
        }.get(operation.category_source or "", "по ключевым словам")
        facts.append(f"🏷 {category.title} · <i>{origin}</i>")

        if operation.shares:
            parts = len(operation.shares)
            per_person = money(operation.shares[0].amount)
            names = ", ".join(esc(share.user.short_name) for share in operation.shares)
            if members_total is not None and parts < members_total:
                facts.append(f"👥 Делим на {parts} по {per_person} · {names}")
            else:
                facts.append(f"👥 Делим на {parts} поровну — по {per_person}")
    else:
        head = header or f"💰 <b>Взнос в фонд</b> — <b>{money(operation.amount)}</b>"
        if operation.title:
            facts.append(f"📝 {esc(operation.title)}")

    where = f" · {esc(group.title)}" if group else ""
    facts.append(
        f"✍️ {esc(operation.author.short_name)} · "
        f"{periods.format_date(operation.occurred_at)}{where} · <code>#{operation.id}</code>"
    )

    card = f"{head}\n{quote(chr(10).join(facts))}"
    if fund_left is not None:
        card += f"\n💼 В фонде: <b>{money(fund_left)}</b>"
    return card


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
    """Черновик из inline-режима — до подтверждения кнопкой."""
    facts: list[str] = []

    if kind == "contribution":
        head = f"💰 <b>Взнос в фонд</b> — <b>{money(amount)}</b>"
    else:
        category_obj = cat.get(category)
        head = f"{category_obj.emoji} <b>{esc(title)}</b> — <b>{money(amount)}</b>"
        origin = "определил ИИ" if category_source == "llm" else "по ключевым словам"
        if category_source == "manual":
            origin = "выбрана вручную"
        facts.append(f"🏷 {category_obj.title} · <i>{origin}</i>")

    facts.append(f"💼 {esc(group_title)}")
    if author_name:
        facts.append(f"✍️ {esc(author_name)}")

    return f"{head}\n{quote(chr(10).join(facts))}\n⏳ <i>Черновик — подтвердите кнопкой ниже.</i>"


def summary_text(data: GroupSummary, *, with_header: bool = True) -> str:
    head = f"💼 <b>{esc(data.group.title)}</b>\n" if with_header else ""
    lines = [
        f"{head}Осталось в фонде: <b>{money(data.fund_left)}</b>",
        quote(
            f"Внесено: {money(data.total_contributed)}\n"
            f"Потрачено: {money(data.total_spent)}"
        ),
    ]

    if not data.members:
        lines.append("\n<i>Пока нет ни одной операции.</i>")
        return "\n".join(lines)

    rows = []
    for item in data.members:
        mark = "🟢" if item.balance > 0 else ("🔴" if item.balance < 0 else "⚪️")
        rows.append(
            f"{mark} {esc(item.user.short_name)} — <b>{signed(item.balance)}</b>\n"
            f"<i>вклад {money(item.contributed)} · доля {money(item.spent)}</i>"
        )

    lines.append(f"\n<b>Балансы</b>\n{quote(chr(10).join(rows))}")

    debtors = [item for item in data.members if item.balance < 0]
    if debtors:
        who = " · ".join(
            f"{esc(item.user.short_name)} {money(-item.balance)}" for item in debtors
        )
        lines.append(f"🔴 <b>Нужно доложить:</b> {who}")

    return "\n".join(lines)


def operations_text(operations: list[Operation], *, title: str, empty: str) -> str:
    if not operations:
        return f"{title}\n\n<i>{empty}</i>"
    body = "\n".join(operation_line(operation) for operation in operations)
    # Длинный список сворачиваем, чтобы он не занимал весь экран.
    return f"{title}\n{quote(body, expandable=len(operations) > 4)}"


def stats_caption(*, group_title: str, mode: str, period_title: str, total: int) -> str:
    what = "по категориям" if mode == "categories" else "по людям"
    return (
        f"📊 <b>Расходы {what}</b>\n"
        f"<i>{esc(group_title)} · {period_title}</i>\n"
        f"Всего: <b>{money(total)}</b>"
    )


def help_text(bot_username: str | None = None) -> str:
    mention = f"@{bot_username}" if bot_username else "@бот"
    blocks = [
        "💼 <b>Общий бюджет</b>",
        "Все скидываются в общий фонд, покупки списываются из него. "
        "Баланс каждого — сколько внёс минус его доля расходов.",
        "",
        "<b>В личке</b>",
        quote(
            "Кнопки меню: внести, записать покупку, статистика, свои операции.\n"
            "Можно и текстом: <code>молоко хлеб 850</code>"
        ),
        "<b>В группе</b>",
        quote(
            "<code>/add 5000</code> — взнос в фонд\n"
            "<code>/buy молоко хлеб 850</code> — покупка\n"
            "<code>/balance</code> — балансы участников\n"
            "<code>/stats категории</code> — диаграмма\n"
            "<code>/ops</code> — последние операции\n"
            "<code>/join</code> — присоединиться к бюджету чата"
        ),
        "<b>В любом чате — inline</b>",
        quote(
            f"<code>{mention} 850 молоко хлеб</code> — покупка\n"
            f"<code>{mention} внёс 5000</code> — взнос\n"
            f"<code>{mention} стата категории</code> — диаграмма\n"
            f"<code>{mention} стата люди</code> — по людям\n"
            f"<code>{mention} баланс</code> — балансы"
        ),
    ]

    if settings.web_enabled:
        blocks.append("<b>Веб-версия</b>")
        blocks.append(
            quote(
                "<code>/web</code> в личке — одноразовая ссылка на мини-аппу.\n"
                "Она же открывается в обычном браузере."
            )
        )

    blocks.append(
        "<i>Свою операцию можно поправить или удалить кнопками на её карточке "
        "или через </i><code>/ops</code><i>.</i>"
    )
    return "\n".join(blocks)
