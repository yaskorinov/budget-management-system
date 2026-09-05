import asyncio
import datetime as dt
import os
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
TMP = ROOT / "data" / "tests"
TMP.mkdir(parents=True, exist_ok=True)
db = TMP / "handlers.db"
if db.exists(): db.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db.as_posix()}"
os.environ["LLM_PROVIDER"] = "off"
os.environ["BOT_TOKEN"] = "123456:AAHtesttoken"
os.environ["PUBLIC_BASE_URL"] = "https://budget.example.com"

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (CallbackQuery, Chat, InlineQuery, Message, Update,
                           User as TgUser)
from md_check import validate
from mock_tg import MockSession

from app.bot.bot import create_dispatcher
from app.db.base import init_db

session = MockSession()
bot = Bot(token=os.environ["BOT_TOKEN"], session=session,
          default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = create_dispatcher()

ANYA = TgUser(id=101, is_bot=False, first_name="Аня", username="anya")
BORYA = TgUser(id=102, is_bot=False, first_name="Боря")
PRIVATE = Chat(id=101, type="private")
GROUP = Chat(id=-1001, type="supergroup", title="Квартира на Лесной")
_uid = [0]

def upd(**kw):
    _uid[0] += 1
    return Update(update_id=_uid[0], **kw)

def msg(text, user=ANYA, chat=PRIVATE, reply_to=None, message_id=None):
    _uid[0] += 1
    m = Message(message_id=message_id or _uid[0], date=dt.datetime.now(), chat=chat,
                from_user=user, text=text, reply_to_message=reply_to)
    return m.as_(bot)

async def send(text, user=ANYA, chat=PRIVATE, reply_to=None):
    session.reset()
    await dp.feed_update(bot, upd(message=msg(text, user, chat, reply_to)))
    assert_markup_ok(f"send({text!r})")
    return session.texts()


def bot_msg(message_id, chat=GROUP):
    """Сообщение бота — на него отвечают в группе."""
    return Message(message_id=message_id, date=dt.datetime.now(), chat=chat,
                   from_user=TgUser(id=999, is_bot=True, first_name="Bot"), text="?")

async def press(data, user=ANYA, chat=PRIVATE, inline_message_id=None):
    session.reset()
    cb = CallbackQuery(
        id=str(_uid[0]), from_user=user, chat_instance="x", data=data,
        message=None if inline_message_id else msg("карточка", user, chat),
        inline_message_id=inline_message_id,
    ).as_(bot)
    await dp.feed_update(bot, upd(callback_query=cb))
    assert_markup_ok(f"press({data!r})")
    return session.texts()

async def inline(query, user=ANYA):
    session.reset()
    await dp.feed_update(bot, upd(inline_query=InlineQuery(
        id="iq1", from_user=user, query=query, offset="", chat_type="supergroup")))
    return session.calls

def assert_markup_ok(where: str) -> None:
    """Всё, что бот отправил за последний шаг, должно пройти разбор MarkdownV2."""
    for name, data in session.calls:
        if name == "AnswerCallbackQuery":
            continue  # всплывающая подсказка показывается как есть, без разбора

        rich = (data.get("rich_message") or {}).get("markdown")
        if rich:
            errors = validate(rich)
            assert not errors, f"{where} -> {name}: {errors}" + chr(10) + rich

        for result in data.get("results", []):
            content = result.get("input_message_content") or {}
            markdown = (content.get("rich_message") or {}).get("markdown")
            if markdown:
                errors = validate(markdown)
                assert not errors, f"{where} -> inline: {errors}" + chr(10) + markdown


def show(title, lines):
    print(f"\n### {title}")
    for line in lines:
        print("   ", line.replace("\n", "\n    ")[:400])


async def main():
    await init_db()

    show("/start в группе (join)", await send("/start", ANYA, GROUP))
    show("/join второй участник", await send("/join", BORYA, GROUP))
    show("/add 5000 в группе", await send("/add 5000", ANYA, GROUP))
    show("свободный текст «внёс 3000»", await send("внёс 3000", BORYA, GROUP))
    show("/buy в группе", await send("/buy молоко хлеб яйца 850", ANYA, GROUP))
    show("свободный текст «купил туалетку 450»", await send("купил туалетку и фейри 450", BORYA, GROUP))
    show("/balance", await send("/balance", ANYA, GROUP))
    show("/stats категории", await send("/stats категории", ANYA, GROUP))
    show("/stats люди неделя", await send("/stats люди неделя", ANYA, GROUP))
    show("/ops", await send("/ops", ANYA, GROUP))

    show("/start в личке", await send("/start"))
    show("покупка текстом в личке", await send("новый чайник bosch 3500"))
    show("непонятный текст", await send("привет как дела"))

    # меню в личке
    show("меню: внести", await press("m:add"))
    show("сумма взноса", await send("7500"))
    show("меню: покупка", await press("m:buy"))
    show("текст покупки", await send("порошок и губки 540"))
    show("меню: статистика", await press("m:stats"))
    show("переключение на людей", await press("s:people:month"))
    show("меню: операции", await press("m:ops"))
    show("меню: группа", await press("m:group"))
    show("меню: помощь", await press("m:help"))
    show("меню: домой", await press("m:home"))

    # правка операции №5 (покупка молока, автор Аня)
    show("карточка операции", await press("o:card:5:"))
    show("смена категории -> экран", await press("o:cat:5:"))
    show("установка категории", await press("o:setcat:5:household"))
    show("участники", await press("o:parts:5:"))
    show("исключить участника 2", await press("o:toggle:5:2"))
    show("вернуть участника 2", await press("o:toggle:5:2"))
    show("правка суммы (запрос)", await press("o:amount:5:"))
    show("правка суммы (ввод)", await send("999"))
    show("чужая операция (Боря правит Анину)", await press("o:cat:5:", BORYA))
    show("удаление: подтверждение", await press("o:del:5:"))
    show("удаление: да", await press("o:delyes:5:"))

    # inline
    calls = await inline("850 молоко и хлеб")
    show("inline покупка", [f"{n}: {[r.get('title') for r in d.get('results', [])]}" for n, d in calls])
    draft_id = calls[0][1]["results"][0]["id"]
    show("inline: подтверждение", await press(f"d:ok:{draft_id}:", ANYA, inline_message_id="inline1"))
    show("inline: повторное подтверждение", await press(f"d:ok:{draft_id}:", ANYA, inline_message_id="inline1"))

    calls = await inline("внёс 2000")
    show("inline взнос", [f"{[r.get('title') for r in d.get('results', [])]}" for n, d in calls])
    calls = await inline("стата категории")
    show("inline стата категории", [f"{[r.get('title') for r in d.get('results', [])]}" for n, d in calls])
    calls = await inline("стата люди месяц")
    show("inline стата люди", [f"{[r.get('title') for r in d.get('results', [])]}" for n, d in calls])
    calls = await inline("баланс")
    show("inline баланс", [str(d.get("results", [{}])[0].get("input_message_content", {}).get("message_text", ""))[:300] for n, d in calls])
    calls = await inline("")
    show("inline подсказки", [f"{[r.get('title') for r in d.get('results', [])]}" for n, d in calls])
    calls = await inline("просто текст без суммы")
    show("inline без суммы", [f"{[r.get('title') for r in d.get('results', [])]}" for n, d in calls])

    show("смена категории черновика", await press(f"d:cat:{draft_id}:", ANYA, inline_message_id="inline1"))

    show("повторный /join", await send("/join", BORYA, GROUP))
    show("/newgroup", await send("/newgroup Дача"))
    show("/groups", await send("/groups"))
    show("выбор группы 1", await press("g:pick:1"))
    show("покупка в активной группе", await send("вода 300"))
    show("/members", await send("/members"))
    show("/web без PUBLIC_BASE_URL", await send("/web"))


    # ------------------------------------------------------------------ #
    #  Регрессии: двухшаговый ввод и отмена в группе
    # ------------------------------------------------------------------ #
    from aiogram.fsm.storage.base import StorageKey

    print("\n" + "=" * 60 + "\nРЕГРЕССИИ\n" + "=" * 60)
    key = StorageKey(bot_id=bot.id, chat_id=GROUP.id, user_id=ANYA.id)

    # /buy@bot без аргументов -> ForceReply ответом на команду
    session.reset()
    await dp.feed_update(bot, upd(message=msg("/buy@budget_bot", ANYA, GROUP)))
    sent = session.find("SendRichMessage")[-1]
    assert sent.get("reply_parameters") or sent.get("reply_to_message_id"), \
        "приглашение должно быть ответом на команду, иначе selective не сработает"
    assert sent["reply_markup"].get("force_reply"), "в группе нужен ForceReply"
    show("/buy@bot в группе", session.texts())

    prompt_id = (await dp.storage.get_data(key))["prompt_id"]
    assert prompt_id, "бот обязан запомнить своё приглашение"

    # постороннее сообщение в группе приглашение не перехватывает
    out = await send("просто болтаем в чате", ANYA, GROUP)
    assert out == [], f"чужая реплика не должна становиться покупкой: {out}"
    print("\n### постороннее сообщение в группе — проигнорировано ✓")

    # ответ на приглашение — записывает покупку и убирает приглашение
    session.reset()
    await dp.feed_update(bot, upd(message=msg(
        "яблоки и груши 640", ANYA, GROUP, reply_to=bot_msg(prompt_id))))
    out = session.texts()
    show("ответ на приглашение", out)
    assert any("640" in text for text in out), "ответ должен создать покупку"
    assert session.find("DeleteMessage"), "приглашение убирается после ответа"

    # отмена в личке возвращает меню
    await send("/buy")
    show("отмена в личке", await press("m:cancel"))

    # отмена в группе удаляет сообщение и отвечает на колбэк
    session.reset()
    cb = CallbackQuery(id="c1", from_user=ANYA, chat_instance="x", data="m:cancel",
                       message=msg("Сколько внести?", ANYA, GROUP)).as_(bot)
    await dp.feed_update(bot, upd(callback_query=cb))
    assert session.find("DeleteMessage"), "отмена в группе должна удалять сообщение"
    assert session.find("AnswerCallbackQuery"), "колбэк обязан получить ответ"
    print("### отмена в группе: DeleteMessage + ответ на колбэк ✓")

    # «В меню» на карточке в группе: без web_app-кнопки (иначе BUTTON_TYPE_INVALID)
    session.reset()
    cb = CallbackQuery(id="c2", from_user=ANYA, chat_instance="x", data="m:home",
                       message=msg("карточка", ANYA, GROUP)).as_(bot)
    await dp.feed_update(bot, upd(callback_query=cb))
    keyboard = session.find("EditMessageText")[-1]["reply_markup"]
    buttons = [b for row in keyboard["inline_keyboard"] for b in row]
    assert not any(b.get("web_app") for b in buttons), "web_app-кнопка вне лички недопустима"
    print("### меню в группе — без web_app-кнопки ✓")

    # упавший хендлер всё равно обязан ответить на колбэк, иначе кнопка «висит»
    import logging

    logging.getLogger("app.bot.bot").setLevel(logging.CRITICAL)  # падение здесь ожидаемое
    session.reset()
    session.fail_on.add("EditMessageText")
    cb = CallbackQuery(id="c3", from_user=ANYA, chat_instance="x", data="m:home",
                       message=msg("карточка", ANYA, GROUP)).as_(bot)
    await dp.feed_update(bot, upd(callback_query=cb))
    assert session.find("AnswerCallbackQuery"), "после ошибки колбэк остался без ответа"
    logging.getLogger("app.bot.bot").setLevel(logging.NOTSET)
    print("### ошибка в хендлере — колбэк всё равно отвечен ✓")

    # ---- статистика ----
    from aiogram.types import PhotoSize

    def photo_msg(chat, markup, message_id=5555):
        """Сообщение-диаграмма: у него нет text, только caption."""
        return Message(
            message_id=message_id, date=dt.datetime.now(), chat=chat,
            from_user=TgUser(id=999, is_bot=True, first_name="Bot"),
            caption="📊 Расходы", reply_markup=markup,
            photo=[PhotoSize(file_id="f", file_unique_id="u", width=800, height=450)],
        )

    async def press_on(message, data, user=ANYA):
        session.reset()
        cb = CallbackQuery(id="x", from_user=user, chat_instance="x",
                           data=data, message=message).as_(bot)
        await dp.feed_update(bot, upd(callback_query=cb))
        return session

    # в группе у диаграммы «Удалить», в личке — «В меню»
    session.reset()
    await dp.feed_update(bot, upd(message=msg("/stats категории", ANYA, GROUP)))
    group_kb = session.find("SendPhoto")[-1]["reply_markup"]
    group_buttons = [b["text"] for row in group_kb["inline_keyboard"] for b in row]
    assert "🗑 Удалить" in group_buttons, group_buttons
    assert "⬅️ В меню" not in group_buttons, "личное меню в общем чате ни к чему"

    session.reset()
    await dp.feed_update(bot, upd(message=msg("/stats категории", ANYA, PRIVATE)))
    private_kb = session.find("SendPhoto")[-1]["reply_markup"]
    private_buttons = [b["text"] for row in private_kb["inline_keyboard"] for b in row]
    assert "⬅️ В меню" in private_buttons, private_buttons
    print("\n### клавиатура диаграммы: в группе «Удалить», в личке «В меню» ✓")

    # «В меню» на диаграмме в личке: сообщение с картинкой заменяется текстовым
    from aiogram.types import InlineKeyboardMarkup
    kb = InlineKeyboardMarkup.model_validate(private_kb)
    out = await press_on(photo_msg(PRIVATE, kb), "m:home")
    assert out.find("DeleteMessage"), "картинку нельзя править текстом — её заменяют"
    assert out.find("SendRichMessage"), "меню должно прийти новым сообщением"
    assert out.find("AnswerCallbackQuery"), "колбэк без ответа — кнопка «висит»"
    print("### «В меню» на диаграмме: старое сообщение удалено, меню отправлено ✓")

    # «Удалить» на диаграмме в группе
    kb_group = InlineKeyboardMarkup.model_validate(group_kb)
    out = await press_on(photo_msg(GROUP, kb_group), "m:close")
    assert out.find("DeleteMessage"), "кнопка «Удалить» должна убирать диаграмму"
    print("### «Удалить» в группе убирает диаграмму ✓")

    # повторное нажатие на уже выбранную категорию — без обращения к Telegram
    active = next(b for row in kb.inline_keyboard for b in row
                  if (b.text or "").startswith("• "))
    out = await press_on(photo_msg(PRIVATE, kb), active.callback_data)
    answers = out.find("AnswerCallbackQuery")
    assert answers and answers[0].get("text") == "Уже показано", answers
    assert not out.find("EditMessageMedia"), "нечего перерисовывать — правки быть не должно"
    print("### повторное нажатие активной кнопки: подсказка вместо ошибки ✓")

    # переключение на другую категорию по-прежнему перерисовывает диаграмму
    other = next(b for row in kb.inline_keyboard for b in row
                 if b.callback_data and b.callback_data.startswith("s:people"))
    out = await press_on(photo_msg(PRIVATE, kb), other.callback_data)
    assert out.find("EditMessageMedia"), "смена среза должна обновлять картинку"
    print("### смена среза обновляет диаграмму ✓")

    # ---- /web выдаёт ссылку входа только в личке ----
    from sqlalchemy import func, select

    from app.db.base import session_scope
    from app.db.models import WebLoginToken

    async def tokens_issued() -> int:
        async with session_scope() as db:
            return int(await db.scalar(select(func.count(WebLoginToken.id))) or 0)

    before = await tokens_issued()
    out = await send("/web", ANYA, GROUP)
    show("/web в группе", out)
    assert await tokens_issued() == before, "в группе токен создаваться не должен"
    assert not any("login=" in text for text in out), "ссылка входа утекла в общий чат"
    button = session.find("SendRichMessage")[-1]["reply_markup"]["inline_keyboard"][0][0]
    assert button["url"].endswith("?start=web"), button
    print("\n### /web в группе: ни токена, ни ссылки — только кнопка в личку ✓")

    out = await send("/web", ANYA, PRIVATE)
    assert await tokens_issued() == before + 1, "в личке ссылка должна выдаваться"
    assert any("login=" in text for text in out), out
    print("### /web в личке выдаёт ссылку ✓")

    # диплинк из кнопки: /start web в личке — ссылка, в группе — обычный join
    out = await send("/start web", ANYA, PRIVATE)
    assert await tokens_issued() == before + 2 and any("login=" in t for t in out)
    out = await send("/start web", ANYA, GROUP)
    assert await tokens_issued() == before + 2, "диплинк в группе не должен выдавать токен"
    assert not any("login=" in text for text in out), "ссылка утекла через /start web"
    print("### диплинк /start web: ссылка только в личке ✓")

    # ---- первый запуск: у человека ещё нет ни одного бюджета ----
    NEWBIE = TgUser(id=777, is_bot=False, first_name="Новичок")
    NEWBIE_CHAT = Chat(id=777, type="private")

    out = await send("/start", NEWBIE, NEWBIE_CHAT)
    show("первый /start", out)
    assert any("нет общего бюджета" in text for text in out), out
    buttons = [b["text"] for row in
               session.find("SendRichMessage")[-1]["reply_markup"]["inline_keyboard"]
               for b in row]
    assert "➕ Создать бюджет" in buttons, buttons
    assert "⬅️ В меню" not in buttons, "меню без бюджета вести некуда"

    # «В меню» объясняет, что делать, а не молчит
    session.reset()
    cb = CallbackQuery(id="n1", from_user=NEWBIE, chat_instance="x", data="m:home",
                       message=msg("нет бюджета", NEWBIE, NEWBIE_CHAT)).as_(bot)
    await dp.feed_update(bot, upd(callback_query=cb))
    answer = session.find("AnswerCallbackQuery")[-1]
    assert answer.get("text") == "Сначала создайте бюджет", answer
    assert answer.get("show_alert"), "подсказку легко не заметить без alert"
    print("\n### «В меню» без бюджета: подсказка «Сначала создайте бюджет» ✓")

    # кнопка «Создать бюджет» спрашивает название
    session.reset()
    cb = CallbackQuery(id="n2", from_user=NEWBIE, chat_instance="x", data="m:newgroup",
                       message=msg("нет бюджета", NEWBIE, NEWBIE_CHAT)).as_(bot)
    await dp.feed_update(bot, upd(callback_query=cb))
    assert_markup_ok("m:newgroup")
    asked = session.find("EditMessageText")[-1]["rich_message"]["markdown"]
    assert "Как его назвать" in asked, asked
    show("кнопка «Создать бюджет»", session.texts())

    # название сообщением — бюджет создан, дальше сразу меню
    out = await send("Квартира на Лесной", NEWBIE, NEWBIE_CHAT)
    show("название бюджета", out)
    assert any("создан" in text for text in out), out
    assert any("Осталось в фонде" in text for text in out), "после создания показываем меню"

    # /newgroup без аргументов тоже спрашивает название, а не показывает синтаксис
    out = await send("/newgroup", NEWBIE, NEWBIE_CHAT)
    assert any("Как его назвать" in text for text in out), out
    out = await send("Дача", NEWBIE, NEWBIE_CHAT)
    assert any("Дача" in text and "создан" in text for text in out), out
    print("### /newgroup без аргументов спрашивает название ✓")

    # ---- админ чата правит и удаляет чужие операции ----
    from app.db.base import session_scope
    from app.core import service

    # Аня записывает покупку в группе, Боря — админ чата
    out = await send("/buy сковорода 2400", ANYA, GROUP)
    show("покупка Ани", out)
    async with session_scope() as db:
        group = await service.get_or_create_group_for_chat(
            db, tg_chat_id=GROUP.id, title=GROUP.title)
        ops = await service.list_operations(db, group_id=group.id, limit=1)
        op_id = ops[0].id

    # без прав админа чужая операция не правится
    session.chat_admins = set()
    out = await press(f"o:del:{op_id}:", BORYA, GROUP)
    assert any("только тот, кто её внёс" in t for t in out), out
    print("\n### чужую операцию посторонний не удаляет ✓")

    # админ чата — может
    session.chat_admins = {BORYA.id}
    out = await press(f"o:del:{op_id}:", BORYA, GROUP)
    assert any("Удалить" in t for t in out), out
    out = await press(f"o:delyes:{op_id}:", BORYA, GROUP)
    show("админ удаляет чужую операцию", out)
    assert any("удалена" in t for t in out), out

    async with session_scope() as db:
        assert await service.get_operation(db, op_id) is None, "операция должна быть удалена"
    print("### админ чата удаляет чужую операцию ✓")

    # ---- ролики уходят одним сообщением с карточкой ----
    session.reset()
    await dp.feed_update(bot, upd(message=msg("/add 1500", ANYA, GROUP)))
    sent = session.find("SendRichMessage")[-1]
    assert not session.find("SendAnimation"), "ролик не должен идти отдельным сообщением"
    assert sent["rich_message"]["media"][0]["id"] == "topup", sent["rich_message"]
    assert "tg://video?id=topup" in sent["rich_message"]["markdown"]
    assert "Взнос в фонд" in sent["rich_message"]["markdown"], "текст в том же сообщении"
    print("")
    print("### пополнение: ролик и карточка одним сообщением ✓")

    session.reset()
    await dp.feed_update(bot, upd(message=msg("/buy кофе 400", ANYA, GROUP)))
    sent = session.find("SendRichMessage")[-1]
    assert sent["rich_message"]["media"][0]["id"] == "buy", sent["rich_message"]
    print("### покупка: ролик buy в том же сообщении ✓")

    # второй раз файл не перезаливается — уходит file_id
    session.reset()
    await dp.feed_update(bot, upd(message=msg("/buy чай 200", ANYA, GROUP)))
    media = session.find("SendRichMessage")[-1]["rich_message"]["media"][0]["media"]
    assert media["media"] == "anim-id", f"ожидался file_id, пришло: {media['media']!r}"
    print("### повторная отправка идёт по file_id ✓")

    # сбой ролика не мешает записать операцию
    from app.bot.common import _gif_ids

    _gif_ids.clear()
    session.reset()
    session.fail_on.add("SendRichMessage")
    logging.getLogger("app.bot.common").setLevel(logging.CRITICAL)
    await dp.feed_update(bot, upd(message=msg("/buy хлеб 90", ANYA, GROUP)))
    logging.getLogger("app.bot.common").setLevel(logging.NOTSET)
    retry = session.find("SendRichMessage")[-1]
    assert not retry["rich_message"].get("media"), "повтор должен уйти без ролика"
    assert "Хлеб" in retry["rich_message"]["markdown"], retry
    print("### упавший ролик не мешает карточке ✓")

    # ---- цвета кнопок ----
    from app.bot import keyboards

    class _Op:
        id, is_purchase, shares = 1, True, []

    styles = {
        button.text: button.style
        for row in keyboards.operation_kb(_Op()).inline_keyboard
        for button in row
    }
    assert styles["🗑 Удалить"] == "danger", styles
    assert styles["✏️ Сумма"] == "primary", styles
    assert styles["✏️ Категория"] == "primary", styles

    confirm = keyboards.confirm_delete_kb(1).inline_keyboard[0]
    assert confirm[0].style == "danger", confirm[0]
    assert confirm[1].style is None, "отмена — обычная кнопка"

    group_stats = keyboards.stats_kb("categories", "month", private=False)
    remove = [b for row in group_stats.inline_keyboard for b in row if "Удалить" in b.text]
    assert remove and remove[0].style == "danger", remove
    print("\n### удаление красное, правка синяя ✓")

    # ---- ролик при правке ----
    from app.bot.common import _gif_ids

    _gif_ids.clear()
    session.reset()
    await dp.feed_update(bot, upd(message=msg("/buy сахар 120", ANYA, GROUP)))
    async with session_scope() as db:
        group = await service.get_or_create_group_for_chat(
            db, tg_chat_id=GROUP.id, title=GROUP.title)
        fresh = (await service.list_operations(db, group_id=group.id, limit=1))[0].id

    session.reset()
    await press(f"o:setcat:{fresh}:household", ANYA, GROUP)
    edited = session.find("EditMessageText")[-1]["rich_message"]
    assert edited["media"][0]["id"] == "edit", edited
    assert "tg://video?id=edit" in edited["markdown"]
    assert "Категория обновлена" in edited["markdown"], edited["markdown"]
    print("\n### смена категории: карточка с роликом edit ✓")

    # правка суммы обновляет карточку разметкой, а не сырым текстом
    session.reset()
    await press(f"o:amount:{fresh}:", ANYA, GROUP)
    prompt_id = session.find("EditMessageText")[-1].get("message_id")
    session.reset()
    await dp.feed_update(bot, upd(message=msg("777", ANYA, GROUP, reply_to=bot_msg(prompt_id))))
    card = session.find("EditMessageText")[-1]
    assert "rich_message" in card and "text" not in card, "карточка должна уходить rich-разметкой"
    assert card["rich_message"]["media"][0]["id"] == "edit", card["rich_message"]
    print("### правка суммы: карточка rich-разметкой и с роликом ✓")

    # карточка с роликом правится на месте, а не пересоздаётся

    # ролик возвращается при выходе из правки
    from app.bot.common import _card_gifs

    session.reset()
    sent = await dp.feed_update(bot, upd(message=msg("/buy соль 60", ANYA, GROUP)))
    card = session.find("SendRichMessage")[-1]
    card_id = [m for n, m in session.calls if n == "SendRichMessage"]
    assert card["rich_message"]["media"][0]["id"] == "buy"
    # id отправленного сообщения знает мок: он вернул его хендлеру
    key = next(k for k in _card_gifs if _card_gifs[k] == "buy")

    # заходим в выбор категории и возвращаемся назад
    message_id = int(key[1])
    for data in (f"o:cat:{fresh}:", f"o:card:{fresh}:"):
        session.reset()
        cb = CallbackQuery(
            id="b1", from_user=ANYA, chat_instance="x", data=data,
            message=Message(message_id=message_id, date=dt.datetime.now(), chat=GROUP,
                            from_user=TgUser(id=999, is_bot=True, first_name="Bot")),
        ).as_(bot)
        await dp.feed_update(bot, upd(callback_query=cb))
        media = session.find("EditMessageText")[-1]["rich_message"].get("media")
        assert media and media[0]["id"] == "buy", f"{data}: ролик потерялся ({media})"
    print("\n### при выходе из правки ролик возвращается ✓")

    # ---- голосовой ввод ----
    import io

    from aiogram.types import Voice

    from app.config import settings as app_settings
    from app.core import voice as stt

    def voice_msg(text_id, user=ANYA, chat=PRIVATE):
        _uid[0] += 1
        m = Message(message_id=_uid[0], date=dt.datetime.now(), chat=chat, from_user=user,
                    voice=Voice(file_id=text_id, file_unique_id="u", duration=3,
                                mime_type="audio/ogg", file_size=2048))
        return m.as_(bot)

    heard = {"text": "купил молоко и хлеб 850"}

    async def fake_transcribe(audio, fmt="ogg"):
        assert fmt == "ogg", f"формат Telegram — ogg, пришло {fmt}"
        return heard["text"]

    async def fake_download(self, path, destination, **kwargs):
        destination.write(b"fake-audio")
        return destination

    real_transcribe = stt.transcribe
    stt.transcribe = fake_transcribe
    import app.bot.handlers.voice as voice_module
    voice_module.stt.transcribe = fake_transcribe
    bot.download_file = fake_download.__get__(bot)

    # Включаем ветку с расшифровкой. Категоризацию при этом в сеть не пускаем:
    # подменяем запрос так, чтобы сработал словарный фолбэк
    from app.core import classifier

    async def no_network(*args, **kwargs):
        raise RuntimeError("в тестах сети нет")

    real_chat = classifier.chat
    classifier.chat = no_network
    logging.getLogger("app.core.classifier").setLevel(logging.CRITICAL)
    app_settings.llm_provider = "openai_compat"
    app_settings.llm_api_key = "test-key"

    session.reset()
    await dp.feed_update(bot, upd(message=voice_msg("voice-1")))
    out = session.texts()
    show("голосовое: покупка", out)
    assert any("Услышал" in t for t in out), out
    assert any("850" in t.replace(" ", " ") for t in out), out

    heard["text"] = "внёс пять тысяч 5000"
    session.reset()
    await dp.feed_update(bot, upd(message=voice_msg("voice-2")))
    out = session.texts()
    assert any("Взнос в фонд" in t for t in out), out
    print("\n### голос: покупка и взнос записываются ✓")

    # не разобрали — операция не создаётся
    async def silent(audio, fmt="ogg"):
        return None

    voice_module.stt.transcribe = silent
    session.reset()
    await dp.feed_update(bot, upd(message=voice_msg("voice-3")))
    assert any("Не разобрал" in t for t in session.texts()), session.texts()
    print("### неразборчивую запись бот не выдумывает ✓")

    # ---- ежедневные сообщения ----
    import datetime as _dt

    from app.bot import scheduler
    from app.core import insights

    async def fake_tip(session_, group_):
        return "Продукты съедают половину бюджета — попробуйте закупаться раз в неделю."

    async def fake_note():
        return "Фонд немного просел — пополните, когда будет удобно."

    real_tip, real_note = insights.spending_tip, insights.debt_note
    scheduler.insights.spending_tip = fake_tip
    scheduler.insights.debt_note = fake_note
    app_settings.tips_hour, app_settings.debts_hour = 9, 9

    session.reset()
    morning = _dt.datetime(2026, 9, 10, 9, 30)
    sent = await scheduler.run_due_jobs(bot, now=morning)
    texts_sent = [
        (data.get("rich_message") or {}).get("markdown", "")
        for name, data in session.calls if name == "SendRichMessage"
    ]
    show("ежедневные сообщения", texts_sent)
    assert any("Совет по расходам" in t for t in texts_sent), texts_sent
    assert any("Общий фонд" in t and "Нужно внести" in t for t in texts_sent), texts_sent
    print(f"\n### совет и напоминание отправлены ({sent}) ✓")

    # второй запуск в тот же день ничего не шлёт
    session.reset()
    again = await scheduler.run_due_jobs(bot, now=morning.replace(hour=23))
    assert again == 0 and not session.find("SendRichMessage"), "дубли за день недопустимы"
    print("### повторно за день не рассылается ✓")

    # на следующий день — снова
    session.reset()
    tomorrow = await scheduler.run_due_jobs(bot, now=morning + _dt.timedelta(days=1))
    assert tomorrow > 0, "на следующий день сообщения должны уйти"
    print("### на следующий день рассылка повторяется ✓")

    # до назначенного часа молчим
    app_settings.tips_hour, app_settings.debts_hour = 20, 20
    session.reset()
    early = await scheduler.run_due_jobs(bot, now=morning + _dt.timedelta(days=2))
    assert early == 0, "до нужного часа рассылки быть не должно"
    print("### до назначенного часа не пишет ✓")

    insights.spending_tip, insights.debt_note = real_tip, real_note


    voice_module.stt.transcribe = real_transcribe
    classifier.chat = real_chat
    logging.getLogger("app.core.classifier").setLevel(logging.NOTSET)
    app_settings.llm_provider = "off"
    app_settings.llm_api_key = ""


    from aiogram.types import Animation

    def card_with_gif(chat, message_id=8888):
        """Карточка, отправленная с медиа-блоком: текста у неё нет."""
        return Message(
            message_id=message_id, date=dt.datetime.now(), chat=chat,
            from_user=TgUser(id=999, is_bot=True, first_name="Bot"),
            animation=Animation(file_id="anim-id", file_unique_id="anim",
                                width=320, height=320, duration=2),
        )

    session.reset()
    cb = CallbackQuery(id="e1", from_user=ANYA, chat_instance="x",
                       data=f"o:parts:{fresh}:", message=card_with_gif(GROUP)).as_(bot)
    await dp.feed_update(bot, upd(callback_query=cb))
    assert not session.find("DeleteMessage"), "карточку с роликом нельзя пересоздавать"
    edited = session.find("EditMessageText")[-1]
    assert edited["message_id"] == 8888, edited
    kept = edited["rich_message"]["media"][0]["media"]
    assert kept["media"] == "anim-id", f"ролик должен сохраниться: {kept}"
    print("\n### карточка с роликом правится на месте ✓")










    print("\nOK: хендлеры отработали")

asyncio.run(main())
