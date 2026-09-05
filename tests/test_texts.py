"""Проверка сообщений: рендерим каждое с враждебными данными и разбираем
как Rich Markdown — так же, как это сделает Telegram."""
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

from md_check import find_unescaped, validate

from app.bot import texts
from app.core import reports, service
from app.core.classifier import parse_purchase
from app.db.base import engine, init_db, session_scope

HOSTILE = "**Хакер** _и_ [Ко] | `код` # (2)"  # имя из Telegram может быть любым


def check(name: str, markup: object, hostile: bool = False) -> None:
    errors = validate(str(markup))
    if hostile:
        errors += find_unescaped(str(markup), HOSTILE)
    assert not errors, f"{name}: {errors}" + chr(10) + str(markup)
    print(f"{chr(10)}=== {name} ==={chr(10)}{markup}")


async def main() -> None:
    await init_db()
    async with session_scope() as s:
        anya = await service.get_or_create_user(s, tg_user_id=1, first_name="Аня")
        borya = await service.get_or_create_user(s, tg_user_id=2, first_name=HOSTILE)
        vika = await service.get_or_create_user(s, tg_user_id=3, first_name="Вика")
        group, _ = await service.get_or_create_group_for_chat(
            s, tg_chat_id=-1, title="Квартира №5 (2-й) [Лесная] *звезда*"
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
                    s, group_id=group.id, author_id=who.id, amount=parsed.amount,
                    category=parsed.category, title=parsed.title,
                    category_source=parsed.source,
                )
            )

        partial = await service.add_purchase(
            s, group_id=group.id, author_id=vika.id, amount=120000,
            category="food", title="Пицца (2 шт.) | 50% скидка",
            participant_ids=[vika.id, borya.id], category_source="manual",
        )

        data = await service.summary(s, group=group)
        members = await service.group_members(s, group.id)

        check("Карточка покупки",
              texts.operation_card(operations[0], group=group,
                                   members_total=len(members), fund_left=data.fund_left))
        check("Покупка на части участников",
              texts.operation_card(partial, group=group, members_total=len(members),
                                   fund_left=data.fund_left), hostile=True)
        check("Карточка взноса",
              texts.operation_card(
                  await service.add_contribution(
                      s, group_id=group.id, author_id=borya.id, amount=250000),
                  group=group, fund_left=data.fund_left), hostile=True)
        check("Сводка и балансы", texts.summary_text(data), hostile=True)
        check("Список операций",
              texts.operations_text(
                  await service.list_operations(s, group_id=group.id, limit=10),
                  title=texts.heading(2, "📒 Операции"), subtitle=group.title,
                  empty="пусто"), hostile=True)
        check("Короткий список",
              texts.operations_text(operations[:2],
                                    title=texts.heading(2, "📒 Мои"), empty="пусто"))
        check("Пустой список",
              texts.operations_text([], title=texts.heading(2, "📒 Мои"), empty="Пока пусто"))
        check("Черновик покупки",
              texts.draft_card(kind="purchase", amount=85000, title="Молоко и хлеб",
                               category="food", group_title=group.title,
                               category_source="llm", author_name=HOSTILE), hostile=True)
        check("Черновик взноса",
              texts.draft_card(kind="contribution", amount=500000, title="",
                               category="other", group_title=group.title,
                               author_name="Аня"))

        report = await reports.build(s, group=group, mode="categories", period="month")
        check("Подпись диаграммы",
              texts.stats_caption(group_title=group.title, mode=report.mode,
                                  period_title=report.period_title, total=report.total))
        check("Таблица расходов", texts.stats_table(report.slices, report.total))
        people = await reports.build(s, group=group, mode="people", period="all")
        check("Расходы по людям", texts.stats_table(people.slices, people.total), hostile=True)
        check("Справка", texts.help_text("budget_bot"))

        from app.bot.common import NO_GROUP_HINT
        check("Нет бюджета", NO_GROUP_HINT)

        # подпись под фотографией разметки не содержит вовсе
        caption = texts.stats_caption_plain(
            group_title=group.title, mode=report.mode,
            period_title=report.period_title, total=report.total)
        # Подпись показывается как есть: ни экранирования, ни разметки
        assert group.title in caption and chr(92) not in caption, caption
        print(chr(10) + "=== Подпись под фото (без разметки) ===" + chr(10) + caption)

    await engine.dispose()
    print(chr(10) + "OK: rich-разметка корректна")


asyncio.run(main())
