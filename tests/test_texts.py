"""Проверка разметки сообщений: Telegram отвергает сообщение целиком,
если разметка сломана, поэтому теги проверяем механически."""
import asyncio
import os
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
TMP = ROOT / "data" / "tests"
TMP.mkdir(parents=True, exist_ok=True)

db = TMP / "texts.db"
if db.exists():
    db.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db.as_posix()}"
os.environ["LLM_PROVIDER"] = "off"
os.environ["PUBLIC_BASE_URL"] = "https://budget.example.com"

from html.parser import HTMLParser

from app.bot import texts
from app.core import reports, service
from app.core.classifier import parse_purchase
from app.db.base import engine, init_db, session_scope

# Что Telegram понимает в режиме HTML
ALLOWED = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "span", "tg-spoiler", "a", "tg-emoji", "code", "pre", "blockquote",
}
VOID = set()


class Checker(HTMLParser):
    """Ловит незакрытые, лишние и криво вложенные теги."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.errors.append(f"недопустимый тег <{tag}>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack:
            self.errors.append(f"закрыт незакрытый <{tag}>")
        elif self.stack[-1] != tag:
            self.errors.append(f"</{tag}> при открытом <{self.stack[-1]}>")
            self.stack.pop()
        else:
            self.stack.pop()

    def finish(self) -> list[str]:
        if self.stack:
            self.errors.append(f"не закрыты: {self.stack}")
        return self.errors


def check(name: str, markup: str) -> None:
    parser = Checker()
    parser.feed(markup)
    errors = parser.finish()
    assert not errors, f"{name}: {errors}\n{markup}"
    # Сырые < и & мимо тегов Telegram тоже не простит
    assert " & " not in markup, f"{name}: неэкранированный амперсанд"
    print(f"\n=== {name} ===\n{markup}")


HOSTILE = '<b>Хакер</b> & "Ко"'  # имя из Telegram может быть любым


async def main() -> None:
    await init_db()
    async with session_scope() as s:
        anya = await service.get_or_create_user(s, tg_user_id=1, first_name="Аня")
        borya = await service.get_or_create_user(s, tg_user_id=2, first_name=HOSTILE)
        vika = await service.get_or_create_user(s, tg_user_id=3, first_name="Вика")
        group = await service.get_or_create_group_for_chat(
            s, tg_chat_id=-1, title='Квартира <на> "Лесной" & Ко'
        )
        for user in (anya, borya, vika):
            await service.ensure_member(s, group_id=group.id, user_id=user.id)

        await service.add_contribution(s, group_id=group.id, author_id=anya.id, amount=1500000)
        await service.add_contribution(s, group_id=group.id, author_id=borya.id, amount=300000)

        operations = []
        for text, who in [
            ("молоко хлеб яйца 1850", anya),
            ("туалетная бумага и фейри 690", borya),
            ("квартплата за август 6200", anya),
            ("нетфликс 799", vika),
            ("новый чайник bosch 3500", borya),
        ]:
            parsed = await parse_purchase(text)
            operations.append(
                await service.add_purchase(
                    s,
                    group_id=group.id,
                    author_id=who.id,
                    amount=parsed.amount,
                    category=parsed.category,
                    title=parsed.title,
                    category_source=parsed.source,
                )
            )

        partial = await service.add_purchase(
            s, group_id=group.id, author_id=vika.id, amount=120000,
            category="food", title="Пицца", participant_ids=[vika.id, borya.id],
            category_source="manual",
        )

        data = await service.summary(s, group=group)
        members = await service.group_members(s, group.id)

        check("Карточка покупки",
              texts.operation_card(operations[0], group=group,
                                   members_total=len(members), fund_left=data.fund_left))
        check("Покупка на части участников",
              texts.operation_card(partial, group=group,
                                   members_total=len(members), fund_left=data.fund_left))
        check("Карточка взноса",
              texts.operation_card(
                  await service.add_contribution(
                      s, group_id=group.id, author_id=borya.id, amount=250000),
                  group=group, fund_left=data.fund_left))
        check("Сводка и балансы", texts.summary_text(data))
        check("Список операций",
              texts.operations_text(
                  await service.list_operations(s, group_id=group.id, limit=10),
                  title="📒 <b>Операции</b>", empty="пусто"))
        check("Короткий список",
              texts.operations_text(operations[:2], title="📒 <b>Мои</b>", empty="пусто"))
        check("Пустой список",
              texts.operations_text([], title="📒 <b>Мои</b>", empty="Пока пусто"))
        check("Черновик покупки",
              texts.draft_card(kind="purchase", amount=85000, title="Молоко и хлеб",
                               category="food", group_title=group.title,
                               category_source="llm", author_name=HOSTILE))
        check("Черновик взноса",
              texts.draft_card(kind="contribution", amount=500000, title="",
                               category="other", group_title=group.title,
                               author_name="Аня"))

        report = await reports.build(s, group=group, mode="categories", period="month")
        check("Подпись диаграммы",
              texts.stats_caption(group_title=group.title, mode=report.mode,
                                  period_title=report.period_title, total=report.total))
        check("Текстовая статистика", texts.pre(reports.render_text(report)))
        report_people = await reports.build(s, group=group, mode="people", period="all")
        check("Статистика по людям", texts.pre(reports.render_text(report_people)))
        check("Справка", texts.help_text("budget_bot"))

    await engine.dispose()
    print("\nOK: разметка сообщений корректна")


asyncio.run(main())
