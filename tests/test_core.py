import asyncio
import os
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
TMP = ROOT / "data" / "tests"
TMP.mkdir(parents=True, exist_ok=True)
db = TMP / "test.db"
if db.exists(): db.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db.as_posix()}"
os.environ["LLM_PROVIDER"] = "off"

from app.db.base import init_db, session_scope
from app.core import service, reports, periods
from app.core.classifier import parse_purchase

async def main():
    await init_db()
    async with session_scope() as s:
        anya = await service.get_or_create_user(s, tg_user_id=1, first_name="Аня")
        borya = await service.get_or_create_user(s, tg_user_id=2, first_name="Боря")
        vika = await service.get_or_create_user(s, tg_user_id=3, first_name="Вика")
        g = await service.get_or_create_group_for_chat(s, tg_chat_id=-100500, title="Квартира на Лесной")
        for u in (anya, borya, vika):
            await service.ensure_member(s, group_id=g.id, user_id=u.id)

        await service.add_contribution(s, group_id=g.id, author_id=anya.id, amount=1000000)
        await service.add_contribution(s, group_id=g.id, author_id=borya.id, amount=500000)
        await service.add_contribution(s, group_id=g.id, author_id=vika.id, amount=300000)

        for text, author in [("молоко хлеб яйца 850", anya), ("туалетка и фейри 450", borya),
                             ("квартплата за август 6200", anya), ("netflix 799", vika),
                             ("чайник bosch 3500", borya)]:
            p = await parse_purchase(text)
            await service.add_purchase(s, group_id=g.id, author_id=author.id,
                                       amount=p.amount, category=p.category, title=p.title,
                                       category_source=p.source, raw_text=text)

        # покупка только на двоих
        op = await service.add_purchase(s, group_id=g.id, author_id=vika.id, amount=120000,
                                        category="food", title="Пицца",
                                        participant_ids=[vika.id, borya.id])
        print("shares:", [(sh.user.short_name, sh.amount) for sh in op.shares])

        data = await service.summary(s, group=g)
        print(f"фонд={data.fund_left/100:.2f} внесено={data.total_contributed/100:.2f} потрачено={data.total_spent/100:.2f}")
        assert data.fund_left == data.total_contributed - data.total_spent
        assert sum(m.balance for m in data.members) == data.fund_left, "сумма балансов = остаток фонда"
        for m in data.members:
            print(f"  {m.user.short_name}: внёс {m.contributed/100:.2f} доля {m.spent/100:.2f} баланс {m.balance/100:+.2f}")

        # правка: сумма и категория
        await service.edit_operation(s, op, amount=150000, category="other")
        assert sum(sh.amount for sh in op.shares) == 150000
        # исключаем Борю
        await service.edit_operation(s, op, participant_ids=[vika.id])
        assert len(op.shares) == 1 and op.shares[0].amount == 150000

        # удаление
        ops = await service.list_operations(s, group_id=g.id, limit=50)
        print("операций:", len(ops))
        await service.delete_operation(s, ops[0])
        assert len(await service.list_operations(s, group_id=g.id, limit=50)) == len(ops) - 1

        for mode in ("categories", "people"):
            r = await reports.build(s, group=g, mode=mode, period="month")
            print(mode, r.period_title, [(x.label, x.value) for x in r.slices])
            png = reports.render_png(r)
            out = TMP / f"chart_{mode}.png"
            out.write_bytes(png)
            print("  png:", len(png), "->", out.name)

        r_all = await reports.build(s, group=g, mode="categories", period="all")
        assert r_all.total > 0
        r_prev = await reports.build(s, group=g, mode="categories", period="prev_month")
        print("прошлый месяц:", r_prev.period_title, "пусто:", r_prev.is_empty)

    # своя SVG-иконка разбирается в путь и вписывается в единичный квадрат
    from app.core.svg_icons import load_icon

    icon = load_icon(str(ROOT / "assets" / "icons" / "food.svg"))
    assert icon is not None and len(icon.vertices) > 10, "иконка не разобралась"
    box = icon.get_extents()
    assert max(box.width, box.height) == 1.0, f"иконка не нормализована: {box}"
    assert abs(box.x0 + box.x1) < 1e-6 and abs(box.y0 + box.y1) < 1e-6, "иконка не по центру"
    print("SVG-иконка:", len(icon.vertices), "вершин, вписана в квадрат")

    print("\nOK: ядро работает")

asyncio.run(main())
