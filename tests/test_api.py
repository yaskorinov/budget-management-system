import asyncio
import os
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
TMP = ROOT / "data" / "tests"
TMP.mkdir(parents=True, exist_ok=True)
db = TMP / "api.db"
if db.exists(): db.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db.as_posix()}"
os.environ["LLM_PROVIDER"] = "off"
os.environ["BOT_TOKEN"] = ""          # бот не поднимется — веб должен жить
os.environ["SECRET_KEY"] = "test-secret"

from fastapi.testclient import TestClient
from app.api.app import app
from app.api.auth import issue_session
from app.db.base import engine, init_db, session_scope
from app.core import service


async def seed():
    """Готовим данные в отдельном цикле и обязательно закрываем пул:
    соединения aiosqlite привязаны к своему event loop."""
    await init_db()
    async with session_scope() as s:
        a = await service.get_or_create_user(s, tg_user_id=11, first_name="Аня")
        b = await service.get_or_create_user(s, tg_user_id=22, first_name="Боря")
        g = await service.get_or_create_group_for_chat(s, tg_chat_id=-777, title="Тестовая квартира")
        for u in (a, b):
            await service.ensure_member(s, group_id=g.id, user_id=u.id)
        ids = (a.id, b.id, g.id)
    await engine.dispose()
    return ids


aid, bid, gid = asyncio.run(seed())

with TestClient(app) as client:
    h = {"Authorization": f"Bearer {issue_session(aid)}"}

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/me").status_code == 401, "без токена — 401"

    me = client.get("/api/me", headers=h).json()
    print("me:", me["user"]["name"], "| групп:", len(me["groups"]))

    cats = client.get("/api/categories", headers=h).json()
    print("категорий:", len(cats))

    r = client.post(f"/api/groups/{gid}/operations", headers=h,
                    json={"kind": "contribution", "amount": 1000000})
    assert r.status_code == 201, r.text
    r = client.post(f"/api/groups/{gid}/operations", headers=h,
                    json={"kind": "purchase", "amount": 85000, "category": "food",
                          "title": "Продукты", "participant_ids": [aid, bid]})
    assert r.status_code == 201, r.text
    op = r.json()
    print("покупка:", op["id"], op["category_title"], [x["amount"] for x in op["shares"]])

    s = client.get(f"/api/groups/{gid}/summary", headers=h).json()
    print("фонд:", s["fund_left"], "| балансы:", [(m["name"], m["balance"]) for m in s["members"]])
    assert s["fund_left"] == 1000000 - 85000

    r = client.patch(f"/api/operations/{op['id']}", headers=h,
                     json={"amount": 90000, "category": "household"})
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "household"
    assert sum(x["amount"] for x in r.json()["shares"]) == 90000, "доли пересчитались"

    hb = {"Authorization": f"Bearer {issue_session(bid)}"}
    assert client.patch(f"/api/operations/{op['id']}", headers=hb,
                        json={"amount": 1}).status_code == 403, "чужое править нельзя"

    st = client.get(f"/api/groups/{gid}/stats?mode=categories&period=month", headers=h).json()
    print("статистика:", st["period_title"], st["total"], [(x["label"], x["value"]) for x in st["slices"]])

    cz = client.post("/api/categorize", headers=h, json={"text": "туалетка и фейри 450"}).json()
    print("разбор:", cz)
    assert cz["amount"] == 45000 and cz["category"] == "household"

    ops = client.get(f"/api/groups/{gid}/operations", headers=h).json()
    print("операций:", len(ops), "| can_edit:", [o["can_edit"] for o in ops])

    assert client.delete(f"/api/operations/{op['id']}", headers=h).status_code == 204
    assert client.get(f"/api/groups/{gid}/summary", headers=h).json()["fund_left"] == 1000000

    assert client.get("/").status_code == 200
    assert "Общий бюджет" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200
    # Шрифт мини-аппы раздаётся из assets — монтирование легко потерять
    assert client.get("/fonts/inter-regular.woff2").status_code == 200

print("\nOK: API работает")
