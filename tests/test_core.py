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
        g, _ = await service.get_or_create_group_for_chat(s, tg_chat_id=-100500, title="Квартира на Лесной")
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

    # Заголовки запроса обязаны быть в ascii: HTTP кириллицу не принимает,
    # а падение уходило в тихий фолбэк на словарь
    from app.config import settings as app_settings
    from app.core.classifier import _headers

    app_settings.llm_base_url = "https://openrouter.ai/api/v1"
    app_settings.llm_api_key = "test"
    for header, value in _headers().items():
        value.encode("ascii")  # UnicodeEncodeError, если снова кириллица
        header.encode("ascii")
    app_settings.llm_api_key = ""
    print("заголовки запроса: только ascii")

    # Прокси: общий применяется ко всему, отдельный перекрывает его для модели
    app_settings.proxy_url = "socks5://user:secret@1.2.3.4:1080"
    app_settings.llm_proxy_url = ""
    assert app_settings.telegram_proxy == app_settings.proxy_url
    assert app_settings.llm_proxy == app_settings.proxy_url

    app_settings.llm_proxy_url = "socks5://5.6.7.8:1080"
    assert app_settings.llm_proxy == "socks5://5.6.7.8:1080"
    assert app_settings.telegram_proxy == "socks5://user:secret@1.2.3.4:1080"

    app_settings.proxy_url = ""
    assert app_settings.telegram_proxy is None, "без общего прокси Telegram идёт напрямую"
    assert app_settings.llm_proxy == "socks5://5.6.7.8:1080"

    from app.bot.bot import _hide_password

    assert "secret" not in _hide_password("socks5://user:secret@1.2.3.4:1080")
    app_settings.llm_proxy_url = ""
    print("прокси: общий и отдельный для модели, пароль в лог не попадает")

    # Прокси на localhost внутри контейнера не работает — предупреждаем заранее
    import app.config as cfg

    app_settings.proxy_url = "socks5://127.0.0.1:1080"

    class Flags:
        docker = False
        host_net = False

    class FakePath:
        def __init__(self, path):
            pass

        def exists(self):
            return Flags.docker

    real_path, real_host_net = cfg.Path, cfg.in_host_network
    cfg.Path, cfg.in_host_network = FakePath, lambda: Flags.host_net
    try:
        assert cfg.proxy_warning(app_settings) is None, "вне контейнера предупреждать не о чем"
        Flags.docker = True
        assert "docker-compose.proxy.yml" in (cfg.proxy_warning(app_settings) or "")
        Flags.host_net = True
        assert cfg.proxy_warning(app_settings) is None, "в сети хоста localhost ведёт на хост"
        Flags.host_net = False
        app_settings.proxy_url = "socks5://host.docker.internal:1080"
        assert cfg.proxy_warning(app_settings) is None, "правильный адрес — молчим"
    finally:
        cfg.Path, cfg.in_host_network = real_path, real_host_net
        app_settings.proxy_url = ""
    print("прокси в контейнере: подсказка про сеть хоста")



    # Голосовое Telegram приходит в ogg/opus — модели ждут mp3 или wav
    import io as _io

    import numpy as _np
    import soundfile as _sf

    from app.core.voice import to_mp3

    rate = 48000
    tone = (0.3 * _np.sin(_np.linspace(0, 2 * _np.pi * 220 * 3, rate * 3))).astype("float32")
    source = _io.BytesIO()
    _sf.write(source, tone, rate, format="OGG", subtype="OPUS")
    original = source.getvalue()

    converted, fmt = to_mp3(original)
    assert fmt == "mp3", f"ожидался mp3, вышло {fmt!r}"
    assert len(converted) < len(original), "перекодировка должна облегчать запись"
    back, back_rate = _sf.read(_io.BytesIO(converted))
    assert back_rate == 16000, f"речь ждут в 16 кГц, вышло {back_rate}"
    assert back.ndim == 1, "должно быть моно"
    print(f"голос: ogg {len(original)} -> mp3 {len(converted)} байт, 16 кГц моно")

    # битый файл не должен ронять расшифровку
    import logging as _logging

    _logging.getLogger("app.core.voice").setLevel(_logging.CRITICAL)
    same, empty_fmt = to_mp3(b"not audio")
    _logging.getLogger("app.core.voice").setLevel(_logging.NOTSET)
    assert same == b"not audio" and empty_fmt == "", "нераспознанное уходит как есть"



    print("\nOK: ядро работает")


async def split_mode():
    """Режим дележа: кто за кого платил, кто кому остался должен."""
    async with session_scope() as s:
        dima = await service.get_or_create_user(s, tg_user_id=11, first_name="Дима")
        zhenya = await service.get_or_create_user(s, tg_user_id=12, first_name="Женя")
        kostya = await service.get_or_create_user(s, tg_user_id=13, first_name="Костя")
        g = await service.create_group(s, title="Поездка в горы", owner=dima, mode="split")
        for u in (zhenya, kostya):
            await service.ensure_member(s, group_id=g.id, user_id=u.id)
        assert g.is_split

        # В дележе кассы нет: взнос сюда положить нельзя.
        try:
            await service.add_contribution(s, group_id=g.id, author_id=dima.id, amount=1000)
            raise AssertionError("взнос в режиме дележа должен быть отклонён")
        except service.ServiceError:
            pass

        # Дима заплатил за всех, Женя — за себя и Костю.
        await service.add_purchase(s, group_id=g.id, author_id=dima.id, amount=90000,
                                   category="food", title="Продукты в дорогу")
        await service.add_purchase(s, group_id=g.id, author_id=zhenya.id, amount=30000,
                                   category="food", title="Кофе",
                                   participant_ids=[zhenya.id, kostya.id])

        data = await service.summary(s, group=g)
        by_name = {m.user.short_name: m.balance for m in data.members}
        print("дележ:", by_name)
        assert by_name["Дима"] == 60000, by_name
        assert by_name["Женя"] == -15000, by_name
        assert by_name["Костя"] == -45000, by_name
        assert sum(by_name.values()) == 0, "в дележе балансы сходятся в ноль"
        assert data.fund_left == 0, "кассы в этом режиме нет"
        assert data.total_spent == 120000

        plan = {(d.debtor.short_name, d.creditor.short_name): d.amount for d in data.debts}
        print("кто кому:", plan)
        assert plan == {("Костя", "Дима"): 45000, ("Женя", "Дима"): 15000}, plan
        assert len(data.debts) <= len(data.members) - 1, "переводов не больше, чем участников минус один"

        # Костя возвращает долг — перевод гасит его баланс.
        transfer = await service.add_transfer(s, group_id=g.id, author_id=kostya.id,
                                              to_user_id=dima.id, amount=45000)
        assert transfer.is_transfer and transfer.recipient.short_name == "Дима"

        data = await service.summary(s, group=g)
        by_name = {m.user.short_name: m.balance for m in data.members}
        print("после возврата:", by_name)
        assert by_name["Костя"] == 0 and by_name["Дима"] == 15000
        assert sum(by_name.values()) == 0
        assert [(d.debtor.short_name, d.creditor.short_name, d.amount) for d in data.debts] == [
            ("Женя", "Дима", 15000)
        ]
        # Расходы считаем только по покупкам: возврат долга — не трата.
        assert data.total_spent == 120000

        # Себе перевести нельзя, чужому — тоже.
        for kwargs in ({"to_user_id": kostya.id}, {"to_user_id": 999999}):
            try:
                await service.add_transfer(s, group_id=g.id, author_id=kostya.id,
                                           amount=1000, **kwargs)
                raise AssertionError(f"перевод {kwargs} должен быть отклонён")
            except service.ServiceError:
                pass

        # Режим не переключить, пока есть операции чужого вида.
        try:
            await service.set_group_mode(s, group=g, mode="fund")
            raise AssertionError("смена режима с переводами должна быть отклонена")
        except service.ServiceError:
            pass

        # В кассе переводов не бывает.
        fund = await service.create_group(s, title="Квартира", owner=dima)
        assert not fund.is_split
        await service.ensure_member(s, group_id=fund.id, user_id=zhenya.id)
        try:
            await service.add_transfer(s, group_id=fund.id, author_id=dima.id,
                                       to_user_id=zhenya.id, amount=1000)
            raise AssertionError("перевод в режиме кассы должен быть отклонён")
        except service.ServiceError:
            pass

        # Пустой бюджет режим меняет свободно.
        await service.set_group_mode(s, group=fund, mode="split")
        assert fund.is_split

    print("режим дележа: балансы, взаимозачёт и запреты сходятся")




async def accounts():
    """Вход по приглашению, гостевой аккаунт и привязка личностей."""
    async with session_scope() as s:
        host = await service.get_or_create_user(s, tg_user_id=21, first_name="Хозяин")
        g = await service.create_group(s, title="Общий чат", owner=host)
        invite = await service.create_invite(s, group_id=g.id, created_by=host.id)

        guest = await service.create_guest(s, name="Гостья")
        assert guest.is_guest, "у гостя нет ни Telegram, ни Яндекса"
        joined = await service.accept_invite(s, invite=invite, user=guest)
        assert joined.id == g.id and guest.active_group_id == g.id
        assert await service.is_member(s, group_id=g.id, user_id=guest.id)
        assert invite.uses == 1

        # Повторный вход того же человека счётчик не крутит.
        await service.accept_invite(s, invite=invite, user=guest)
        assert invite.uses == 1, "повторное открытие ссылки — не новый участник"

        # Новая ссылка гасит прежнюю: это и есть отзыв.
        again = await service.create_invite(s, group_id=g.id, created_by=host.id)
        assert await service.get_invite(s, invite.token) is None
        assert (await service.get_invite(s, again.token)).id == again.id

        # Привязка Telegram: пустой аккаунт из бота вливается в гостевой.
        empty = await service.get_or_create_user(s, tg_user_id=22, first_name="Новый")
        empty_id = empty.id
        linked = await service.link_telegram(s, web_user=guest, tg_user=empty)
        assert linked.id == guest.id and linked.tg_user_id == 22
        assert await service.get_user(s, empty_id) is None, "пустышка удалена"
        assert not guest.is_guest

        # Второй Telegram к тому же аккаунту не привяжешь.
        other = await service.get_or_create_user(s, tg_user_id=23, first_name="Ещё")
        try:
            await service.link_telegram(s, web_user=guest, tg_user=other)
            raise AssertionError("второй Telegram привязывать нельзя")
        except service.ServiceError:
            pass

        # Аккаунт с историей молча не сливаем.
        busy = await service.get_or_create_user(s, tg_user_id=24, first_name="Занятой")
        await service.create_group(s, title="Своё", owner=busy)
        guest2 = await service.create_guest(s, name="Второй гость")
        try:
            await service.link_telegram(s, web_user=guest2, tg_user=busy)
            raise AssertionError("аккаунт с историей сливать нельзя")
        except service.ServiceError:
            pass

        # Яндекс ID: один идентификатор — один аккаунт.
        await service.attach_yandex(s, user=guest2, yandex_id="ya-1", email="a@ya.ru")
        assert not guest2.is_guest and guest2.email == "a@ya.ru"
        assert (await service.user_by_yandex(s, "ya-1")).id == guest2.id
        guest3 = await service.create_guest(s, name="Третий")
        try:
            await service.attach_yandex(s, user=guest3, yandex_id="ya-1")
            raise AssertionError("чужой Яндекс ID привязывать нельзя")
        except service.ServiceError:
            pass

        # Протухшее и исчерпанное приглашение не работает.
        short = await service.create_invite(s, group_id=g.id, created_by=host.id, ttl_days=0)
        assert await service.get_invite(s, short.token) is None, "срок вышел"
        spent = await service.create_invite(s, group_id=g.id, created_by=host.id, max_uses=1)
        await service.accept_invite(s, invite=spent, user=guest3)
        assert await service.get_invite(s, spent.token) is None, "лимит исчерпан"

    print("аккаунты: приглашение, гость, привязка Telegram и Яндекса")

asyncio.run(main())
asyncio.run(split_mode())
asyncio.run(accounts())
