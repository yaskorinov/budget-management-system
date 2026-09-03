"""Сборка сообщений бота в MarkdownV2 (см. app/bot/markup.py)."""
from __future__ import annotations

from app.bot.markup import Markup, bold, code, esc, italic, join, lines, pre, quote, raw
from app.config import settings
from app.core import categories as cat
from app.core import periods
from app.core.money import format_money
from app.core.service import GroupSummary
from app.db.models import Group, Operation

SYMBOLS = {"RUB": "₽", "USD": "$", "EUR": "€", "KZT": "₸", "BYN": "Br", "UAH": "₴"}
SYMBOL = SYMBOLS.get(settings.currency.upper(), settings.currency)


def money(cents: int) -> str:
    return format_money(cents, SYMBOL)


def signed(cents: int) -> str:
    return ("+" if cents > 0 else "") + money(cents)


def operation_line(operation: Operation) -> Markup:
    """Две строки для списка операций."""
    when = periods.format_date(operation.occurred_at)
    who = operation.author.short_name

    if operation.is_purchase:
        category = cat.get(operation.category)
        head = join(
            category.emoji, " ", bold(money(operation.amount)), " · ",
            operation.title or category.title,
        )
        tail = italic("#", str(operation.id), " · ", category.title, " · ", who, " · ", when)
    else:
        head = join("💰 ", bold(money(operation.amount)), " · взнос в фонд")
        tail = italic("#", str(operation.id), " · ", who, " · ", when)

    return lines(head, tail)


def operation_card(
    operation: Operation,
    *,
    group: Group | None = None,
    header: Markup | None = None,
    members_total: int | None = None,
    fund_left: int | None = None,
) -> Markup:
    """Карточка операции: заголовок с суммой, подробности — в цитате."""
    facts: list[Markup] = []

    if operation.is_purchase:
        category = cat.get(operation.category)
        head = header or join(
            category.emoji, " ", bold(operation.title or category.title),
            " — ", bold(money(operation.amount)),
        )

        origin = {"llm": "определил ИИ", "manual": "выбрана вручную"}.get(
            operation.category_source or "", "по ключевым словам"
        )
        facts.append(join("🏷 ", category.title, " · ", italic(origin)))

        if operation.shares:
            parts = len(operation.shares)
            per_person = money(operation.shares[0].amount)
            names = ", ".join(share.user.short_name for share in operation.shares)
            if members_total is not None and parts < members_total:
                facts.append(join("👥 Делим на ", str(parts), " по ", per_person, " · ", names))
            else:
                facts.append(join("👥 Делим на ", str(parts), " поровну — по ", per_person))
    else:
        head = header or join("💰 ", bold("Взнос в фонд"), " — ", bold(money(operation.amount)))
        if operation.title:
            facts.append(join("📝 ", operation.title))

    where = f" · {group.title}" if group else ""
    facts.append(
        join(
            "✍️ ", operation.author.short_name, " · ",
            periods.format_date(operation.occurred_at), where, " · ",
            code("#" + str(operation.id)),
        )
    )

    card = lines(head, quote(lines(*facts)))
    if fund_left is not None:
        card = lines(card, join("💼 В фонде: ", bold(money(fund_left))))
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
) -> Markup:
    """Черновик из inline-режима — до подтверждения кнопкой."""
    facts: list[Markup] = []

    if kind == "contribution":
        head = join("💰 ", bold("Взнос в фонд"), " — ", bold(money(amount)))
    else:
        category_obj = cat.get(category)
        head = join(
            category_obj.emoji, " ", bold(title), " — ", bold(money(amount))
        )
        origin = {"llm": "определил ИИ", "manual": "выбрана вручную"}.get(
            category_source, "по ключевым словам"
        )
        facts.append(join("🏷 ", category_obj.title, " · ", italic(origin)))

    facts.append(join("💼 ", group_title))
    if author_name:
        facts.append(join("✍️ ", author_name))

    return lines(
        head,
        quote(lines(*facts)),
        join("⏳ ", italic("Черновик — подтвердите кнопкой ниже")),
    )


def summary_text(data: GroupSummary, *, with_header: bool = True) -> Markup:
    blocks: list[Markup] = []
    if with_header:
        blocks.append(join("💼 ", bold(data.group.title)))

    blocks.append(join("Осталось в фонде: ", bold(money(data.fund_left))))
    blocks.append(
        quote(
            lines(
                join("Внесено: ", money(data.total_contributed)),
                join("Потрачено: ", money(data.total_spent)),
            )
        )
    )

    if not data.members:
        blocks.append(join(""))
        blocks.append(italic("Пока нет ни одной операции"))
        return lines(*blocks)

    rows: list[Markup] = []
    for item in data.members:
        mark = "🟢" if item.balance > 0 else ("🔴" if item.balance < 0 else "⚪️")
        rows.append(
            lines(
                join(mark, " ", item.user.short_name, " — ", bold(signed(item.balance))),
                italic("вклад ", money(item.contributed), " · доля ", money(item.spent)),
            )
        )

    blocks.append(join(""))
    blocks.append(bold("Балансы"))
    blocks.append(quote(lines(*rows)))

    debtors = [item for item in data.members if item.balance < 0]
    if debtors:
        who = join(
            *[
                join(item.user.short_name, " ", money(-item.balance))
                for item in debtors
            ],
            sep=" · ",
        )
        blocks.append(join("🔴 ", bold("Нужно доложить"), ": ", who))

    return lines(*blocks)


def operations_text(operations: list[Operation], *, title: Markup, empty: str) -> Markup:
    if not operations:
        return lines(title, join(""), italic(empty))
    body = lines(*[operation_line(operation) for operation in operations])
    # Длинный список сворачиваем, чтобы он не занимал весь экран.
    return lines(title, quote(body, expandable=len(operations) > 4))


def stats_caption(*, group_title: str, mode: str, period_title: str, total: int) -> Markup:
    what = "по категориям" if mode == "categories" else "по людям"
    return lines(
        join("📊 ", bold("Расходы ", what)),
        italic(group_title, " · ", period_title),
        join("Всего: ", bold(money(total))),
    )


def help_text(bot_username: str | None = None) -> Markup:
    mention = f"@{bot_username}" if bot_username else "@бот"
    blocks: list[Markup] = [
        join("💼 ", bold("Общий бюджет")),
        join(
            "Все скидываются в общий фонд, покупки списываются из него. "
            "Баланс каждого — сколько внёс минус его доля расходов."
        ),
        join(""),
        bold("В личке"),
        quote(
            lines(
                join("Кнопки меню: внести, записать покупку, статистика, свои операции."),
                join("Можно и текстом: ", code("молоко хлеб 850")),
            )
        ),
        bold("В группе"),
        quote(
            lines(
                join(code("/add 5000"), " — взнос в фонд"),
                join(code("/buy молоко хлеб 850"), " — покупка"),
                join(code("/balance"), " — балансы участников"),
                join(code("/stats категории"), " — диаграмма"),
                join(code("/ops"), " — последние операции"),
                join(code("/join"), " — присоединиться к бюджету чата"),
            )
        ),
        bold("В любом чате — inline"),
        quote(
            lines(
                join(code(mention + " 850 молоко хлеб"), " — покупка"),
                join(code(mention + " внёс 5000"), " — взнос"),
                join(code(mention + " стата категории"), " — диаграмма"),
                join(code(mention + " стата люди"), " — по людям"),
                join(code(mention + " баланс"), " — балансы"),
            )
        ),
    ]

    if settings.web_enabled:
        blocks.append(bold("Веб-версия"))
        blocks.append(
            quote(
                lines(
                    join(code("/web"), " в личке — одноразовая ссылка на мини-аппу."),
                    join("Она же открывается в обычном браузере."),
                )
            )
        )

    blocks.append(
        join(
            italic("Свою операцию можно поправить или удалить кнопками на её карточке"),
            " или через ",
            code("/ops"),
        )
    )
    return lines(*blocks)
