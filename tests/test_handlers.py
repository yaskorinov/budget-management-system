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

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (CallbackQuery, Chat, InlineQuery, Message, Update,
                           User as TgUser)
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

def msg(text, user=ANYA, chat=PRIVATE, entities=None):
    _uid[0] += 1
    m = Message(message_id=_uid[0], date=dt.datetime.now(), chat=chat,
                from_user=user, text=text)
    return m.as_(bot)

async def send(text, user=ANYA, chat=PRIVATE):
    session.reset()
    await dp.feed_update(bot, upd(message=msg(text, user, chat)))
    return session.texts()

async def press(data, user=ANYA, chat=PRIVATE, inline_message_id=None):
    session.reset()
    cb = CallbackQuery(
        id=str(_uid[0]), from_user=user, chat_instance="x", data=data,
        message=None if inline_message_id else msg("карточка", user, chat),
        inline_message_id=inline_message_id,
    )
    await dp.feed_update(bot, upd(callback_query=cb))
    return session.texts()

async def inline(query, user=ANYA):
    session.reset()
    await dp.feed_update(bot, upd(inline_query=InlineQuery(
        id="iq1", from_user=user, query=query, offset="", chat_type="supergroup")))
    return session.calls

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

    print("\nOK: хендлеры отработали")

asyncio.run(main())
