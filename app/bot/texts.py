"""Сборка сообщений бота в Rich Markdown (см. app/bot/rich.py)."""
from __future__ import annotations

from app.bot.rich import (
    Rich,
    blocks,
    bold,
    bullets,
    code,
    details,
    esc,
    heading,
    italic,
    join,
    lines,
    marked,
    pre,
    quote,
    raw,
    rule,
    table,
)
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


def cmd(command: str) -> Rich:
    """Команда обычным текстом.

    В моноширинном виде Telegram не распознаёт её как команду: по такой не
    нажать, приходится копировать и отправлять руками.
    """
    return esc(command)


def plain(*parts: object) -> str:
    """Текст без разметки — для подписей к фото и всплывающих подсказок."""
    return "".join(str(part) for part in parts)


def operation_card(
    operation: Operation,
    *,
    group: Group | None = None,
    header: Rich | None = None,
    members_total: int | None = None,
    fund_left: int | None = None,
    data: GroupSummary | None = None,
) -> Rich:
    """Карточка операции: заголовок с суммой, подробности — списком."""
    facts: list[Rich] = []

    if operation.is_purchase:
        category = cat.get(operation.category)
        title = join(
            category.emoji, " ", operation.title or category.title,
            " — ", bold(money(operation.amount)),
        )
        origin = {"llm": "определил ИИ", "manual": "выбрана вручную"}.get(
            operation.category_source or "", "по ключевым словам"
        )
        facts.append(join("🏷 ", category.title, " ", italic("(", origin, ")")))

        if operation.shares:
            parts = len(operation.shares)
            per_person = money(operation.shares[0].amount)
            names = ", ".join(share.user.short_name for share in operation.shares)
            if members_total is not None and parts < members_total:
                facts.append(
                    join("👥 На ", str(parts), " — по ", bold(per_person), " · ", names)
                )
            else:
                facts.append(
                    join("👥 Поровну на ", str(parts), " — по ", bold(per_person))
                )
    elif operation.is_transfer:
        recipient = operation.recipient
        title = join("💸 Возврат долга — ", bold(money(operation.amount)))
        facts.append(
            join(
                "➡️ ", operation.author.short_name, " → ",
                recipient.short_name if recipient else "—",
            )
        )
        if operation.title:
            facts.append(join("📝 ", operation.title))
    else:
        title = join("💰 Взнос в фонд — ", bold(money(operation.amount)))
        if operation.title:
            facts.append(join("📝 ", operation.title))

    where = join(" · ", group.title) if group else raw("")
    facts.append(
        join(
            "✍️ ", operation.author.short_name, " · ",
            periods.format_date(operation.occurred_at), where, " · ",
            code("#" + str(operation.id)),
        )
    )

    parts: list[Rich] = [header or heading(3, title), bullets(*facts)]
    footer = _state_line(data, fund_left)
    if footer is not None:
        parts.append(quote(footer))
    return blocks(*parts)


def _state_line(data: GroupSummary | None, fund_left: int | None) -> Rich | None:
    """Подпись под карточкой: в кассе — остаток, при дележе — сумма долгов."""
    if data is not None:
        if data.is_split:
            owed = sum(item.balance for item in data.members if item.balance > 0)
            return join("🧮 Непогашено: ", bold(money(owed)))
        return join("💼 В фонде: ", bold(money(data.fund_left)))
    if fund_left is not None:
        return join("💼 В фонде: ", bold(money(fund_left)))
    return None


def draft_card(
    *,
    kind: str,
    amount: int,
    title: str,
    category: str,
    group_title: str,
    category_source: str = "rules",
    author_name: str = "",
) -> Rich:
    """Черновик из inline-режима — до подтверждения кнопкой."""
    facts: list[Rich] = []

    if kind == "contribution":
        head = join("💰 Взнос в фонд — ", bold(money(amount)))
    else:
        category_obj = cat.get(category)
        head = join(category_obj.emoji, " ", title, " — ", bold(money(amount)))
        origin = {"llm": "определил ИИ", "manual": "выбрана вручную"}.get(
            category_source, "по ключевым словам"
        )
        facts.append(join("🏷 ", category_obj.title, " ", italic("(", origin, ")")))

    facts.append(join("💼 ", group_title))
    if author_name:
        facts.append(join("✍️ ", author_name))

    return blocks(
        heading(3, head),
        bullets(*facts),
        quote(join("⏳ ", marked("Черновик"), " — подтвердите кнопкой ниже")),
    )


def summary_text(
    data: GroupSummary, *, with_header: bool = True, with_debts: bool = True
) -> Rich:
    """Сводка: остаток фонда и таблица балансов."""
    parts: list[Rich] = []
    if with_header:
        parts.append(heading(2, join("💼 ", data.group.title)))

    if data.is_split:
        owed = sum(item.balance for item in data.members if item.balance > 0)
        parts.append(
            lines(
                join("Непогашенных долгов: ", bold(money(owed))),
                italic("расходы за всё время — ", money(data.total_spent)),
            )
        )
    else:
        parts.append(
            lines(
                join("Осталось в фонде: ", bold(money(data.fund_left))),
                italic(
                    "внесено ", money(data.total_contributed),
                    " · потрачено ", money(data.total_spent),
                ),
            )
        )

    if not data.members:
        parts.append(italic("Пока нет ни одной операции"))
        return blocks(*parts)

    rows = []
    for item in data.members:
        mark = "🟢" if item.balance > 0 else ("🔴" if item.balance < 0 else "⚪️")
        rows.append(
            [
                join(mark, " ", item.user.short_name),
                bold(signed(item.balance)),
                money(item.contributed),
                money(item.spent),
            ]
        )
    parts.append(heading(3, "Балансы"))
    parts.append(
        table(
            ["Участник", "Баланс", "Оплатил", "Доля"]
            if data.is_split
            else ["Участник", "Баланс", "Вклад", "Доля"],
            rows,
            align="lrrr",
        )
    )

    if with_debts and data.is_split and data.debts:
        # План взаимозачёта: платить каждому за каждую покупку не нужно.
        parts.append(
            blocks(
                bold("🔴 Кто кому должен"),
                bullets(
                    *[
                        join(
                            debt.debtor.short_name, " → ", debt.creditor.short_name,
                            " — ", bold(money(debt.amount)),
                        )
                        for debt in data.debts
                    ]
                ),
            )
        )
    elif with_debts and not data.is_split:
        debtors = [item for item in data.members if item.balance < 0]
        if debtors:
            parts.append(
                blocks(
                    bold("🔴 Нужно внести"),
                    bullets(
                        *[
                            join(item.user.short_name, " — ", bold(money(-item.balance)))
                            for item in debtors
                        ]
                    ),
                )
            )

    return blocks(*parts)


def operations_rows(operations: list[Operation]) -> Rich:
    """Таблица операций: номер, что это, сумма."""
    rows = []
    for operation in operations:
        category = cat.get(operation.category)
        if operation.is_purchase:
            what = join(
                category.emoji, " ", operation.title or category.title,
                " · ", italic(operation.author.short_name, ", ",
                              periods.format_date(operation.occurred_at)),
            )
        else:
            what = join(
                "💰 взнос в фонд · ",
                italic(operation.author.short_name, ", ",
                       periods.format_date(operation.occurred_at)),
            )
        rows.append([code("#" + str(operation.id)), what, bold(money(operation.amount))])
    return table(["№", "Операция", "Сумма"], rows, align="llr")


def operations_text(
    operations: list[Operation], *, title: Rich, subtitle: str = "", empty: str
) -> Rich:
    if not operations:
        return blocks(title, italic(empty))

    body = operations_rows(operations)
    # Длинный список прячем в сворачиваемый блок, чтобы он не занял весь экран.
    if len(operations) > 4:
        body = details(bold("Показать ", str(len(operations)), " операций"), body)
    parts = [title]
    if subtitle:
        parts.append(italic(subtitle))
    parts.append(body)
    return blocks(*parts)


def stats_table(slices, total: int) -> Rich:
    """Табличная версия диаграммы — когда картинки нет."""
    rows = [
        [
            item.label,
            money(item.value),
            f"{item.value / total * 100:.0f}%",
            "█" * max(1, round(item.value / total * 8)),
        ]
        for item in slices
    ]
    rows.append([bold("Итого"), bold(money(total)), "", ""])
    return table(["Статья", "Сумма", "Доля", ""], rows, align="lrrl")


def stats_caption(*, group_title: str, mode: str, period_title: str, total: int) -> Rich:
    what = "по категориям" if mode == "categories" else "по людям"
    return blocks(
        heading(2, join("📊 Расходы ", what)),
        lines(
            italic(group_title, " · ", period_title),
            join("Всего: ", bold(money(total))),
        ),
    )


def stats_caption_plain(*, group_title: str, mode: str, period_title: str, total: int) -> str:
    """Подпись под фотографией: там разметки нет, только текст."""
    what = "по категориям" if mode == "categories" else "по людям"
    return plain(
        "📊 Расходы ", what, chr(10),
        group_title, " · ", period_title, chr(10),
        "Всего: ", money(total),
    )


MODE_TITLES = {"fund": "🏦 Общая касса", "split": "🧮 Делим расходы"}

MODE_ABOUT = {
    "fund": "Все скидываются в общий фонд, покупки списываются из него. "
            "Баланс каждого — сколько внёс минус его доля расходов.",
    "split": "Общей кассы нет: за покупку платит кто-то один, остальные "
             "становятся ему должны. Баланс каждого — сколько он заплатил "
             "минус его доля. Бот сводит долги и показывает, кто кому "
             "сколько должен отдать.",
}


def mode_prompt(group: Group) -> Rich:
    """Вопрос сразу после создания бюджета: как считать деньги."""
    return blocks(
        heading(2, join("✅ Бюджет «", group.title, "» создан")),
        join("Как считаем деньги?"),
        bullets(
            join(bold(MODE_TITLES["fund"]), " — ", MODE_ABOUT["fund"]),
            join(bold(MODE_TITLES["split"]), " — ", MODE_ABOUT["split"]),
        ),
        italic("Позже режим можно сменить командой /mode, пока операций нет"),
    )


def mode_card(group: Group) -> Rich:
    """Подтверждение выбранного режима."""
    hint = (
        join("Записывайте покупки — бот сам посчитает, кто кому должен. "
             "Долг возвращают командой ", cmd("/add"), ".")
        if group.is_split
        else join("Скидывайтесь в фонд командой ", cmd("/add"),
                  " и записывайте покупки — фонд будет таять.")
    )
    return blocks(
        heading(3, join(MODE_TITLES[group.mode], " — «", group.title, "»")),
        join(MODE_ABOUT[group.mode]),
        quote(hint),
    )


def help_text(bot_username: str | None = None, *, split: bool = False) -> Rich:
    mention = f"@{bot_username}" if bot_username else "@бот"
    mode = "split" if split else "fund"

    inline_rows = [
        [code(mention + " 850 молоко хлеб"), "покупка"],
    ]
    if not split:
        inline_rows.append([code(mention + " внёс 5000"), "взнос"])
    inline_rows += [
        [code(mention + " стата категории"), "диаграмма"],
        [code(mention + " стата люди"), "по людям"],
        [code(mention + " баланс"), "балансы"],
    ]

    parts: list[Rich] = [
        heading(1, join("💼 Общий бюджет · ", MODE_TITLES[mode])),
        join(MODE_ABOUT[mode]),
        heading(2, "В личке"),
        bullets(
            "Кнопки меню: "
            + ("вернуть долг" if split else "внести")
            + ", записать покупку, статистика, свои операции",
            join("Можно и текстом: ", code("молоко хлеб 850")),
        ),
        heading(2, "В группе"),
        table(
            ["Команда", "Что делает"],
            [
                [
                    cmd("/add 5000"),
                    "вернуть долг участнику" if split else "взнос в фонд",
                ],
                [cmd("/buy молоко хлеб 850"), "покупка"],
                [
                    cmd("/balance"),
                    "балансы и кто кому должен" if split else "балансы участников",
                ],
                [cmd("/stats категории"), "круговая диаграмма"],
                [cmd("/ops"), "последние операции"],
                [cmd("/voice"), "записать операцию голосом"],
                [cmd("/join"), "присоединиться к бюджету чата"],
                [cmd("/mode"), "режим расчётов"],
            ],
        ),
        heading(2, "В любом чате — inline"),
        table(["Запрос", "Результат"], inline_rows),
    ]

    if settings.web_enabled:
        parts.append(heading(2, "Веб-версия"))
        parts.append(
            bullets(
                join(cmd("/web"), " в личке — одноразовая ссылка на мини-аппу"),
                "Она же открывается в обычном браузере",
            )
        )

    parts.append(rule())
    parts.append(
        italic(
            "Свою операцию можно поправить или удалить кнопками на её карточке "
            "или через /ops"
        )
    )
    return blocks(*parts)
