"""Сборка презентации на защиту: контент проекта в фирменном шаблоне.

Запуск: python presentation/build_deck.py — пересобирает pptx рядом со
скриптом. Название команды и роли участников правятся в начале файла.

Берём визуальный шаблон как есть — вся графика, шрифты и палитра остаются
его. Меняем только текст в существующих надписях, поэтому оформление не
разъезжается. Лишние слайды удаляем, оставшиеся переставляем в порядке
шаблона содержания.
"""
from __future__ import annotations

import copy
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt

SP = Path(__file__).parent / "assets"
TEMPLATE = Path(r"C:\Users\oguzok\Desktop\Office-PowerPoint-MCP-Server-main\templates\template.pptx")
OUT = Path(__file__).parent / "Общий бюджет — защита.pptx"

TEAM = "НАЗВАНИЕ КОМАНДЫ"   # ← подставьте своё и запустите скрипт заново
REPO = "github.com/yaskorinov/budget-management-system"


# --------------------------------------------------------------------------- #
#  Мелкие помощники
# --------------------------------------------------------------------------- #

def shape(slide, name):
    for sh in slide.shapes:
        if sh.name == name:
            return sh
    raise KeyError(f"нет фигуры {name!r} на слайде")


def put(slide, name, text, *, size=None):
    """Меняет текст, сохраняя оформление первого прогона.

    Лишние прогоны и абзацы убираем: иначе от прежнего текста остаются хвосты.
    """
    frame = shape(slide, name).text_frame
    para = frame.paragraphs[0]
    if not para.runs:
        para.add_run()
    run = para.runs[0]
    run.text = text
    for extra in para.runs[1:]:
        extra._r.getparent().remove(extra._r)
    for extra in list(frame.paragraphs[1:]):
        extra._p.getparent().remove(extra._p)
    if size is not None:
        run.font.size = Pt(size)


def drop(slide, *names):
    for name in names:
        try:
            sh = shape(slide, name)
        except KeyError:
            continue
        sh._element.getparent().remove(sh._element)


def picture(slide, path, left, top, width):
    """Кладёт картинку, сохраняя пропорции."""
    return slide.shapes.add_picture(str(path), Inches(left), Inches(top), width=Inches(width))


def to_front(slide, *names):
    """Поднимает фигуру над остальными: в шаблоне декоративные объекты лежат
    поверх надписей и съедают заголовок."""
    tree = slide.shapes._spTree
    for name in names:
        el = shape(slide, name)._element
        tree.remove(el)
        tree.append(el)


def move(slide, name, *, left=None, top=None, width=None, height=None):
    sh = shape(slide, name)
    if left is not None:
        sh.left = Inches(left)
    if top is not None:
        sh.top = Inches(top)
    if width is not None:
        sh.width = Inches(width)
    if height is not None:
        sh.height = Inches(height)


def brand(slide, logo="TextBox 16", date="TextBox 20"):
    """Подпись команды и дата — на каждом слайде свои имена надписей."""
    try:
        put(slide, logo, TEAM, size=20)
        move(slide, logo, width=6.0)
    except KeyError:
        pass
    try:
        put(slide, date, "Сентябрь 2026")
    except KeyError:
        pass


def keep_only(prs, indexes):
    """Оставляет слайды с указанными номерами (с нуля) в заданном порядке."""
    ids = list(prs.slides._sldIdLst)
    keep = [ids[i] for i in indexes]
    for sld in ids:
        prs.slides._sldIdLst.remove(sld)
    for sld in keep:
        prs.slides._sldIdLst.append(sld)


# --------------------------------------------------------------------------- #
#  Сборка
# --------------------------------------------------------------------------- #

prs = Presentation(TEMPLATE)
s = prs.slides

# Порядок из шаблона содержания: титул, польза, технологии, архитектура,
# репозиторий, реализация, UX, команда, спасибо.
ORDER = [0, 3, 4, 11, 9, 6, 7, 12, 13]

# ---------------------------------------------------------------- 1. титул --
t = s[0]
put(t, "TextBox 6", "ОБЩИЙ БЮДЖЕТ", size=88)
move(t, "TextBox 6", top=1.5, height=2.0)
put(t, "TextBox 13", "AI-ассистент общих расходов", size=34)
put(t, "TextBox 17", "Кейс 2 · Сбер")
put(t, "TextBox 18", "Хакатон ТОП ИИ")
put(t, "TextBox 19", "3 интерфейса · 1 сценарий")
brand(t)
# Правая капсула уходит под 3D-объект — там подпись всё равно не прочесть.
drop(t, "Group 10", "TextBox 14")
to_front(t, "TextBox 6", "TextBox 13", "TextBox 17", "TextBox 18", "TextBox 19")

# ------------------------------------------------- 2. польза и ценность --
p = s[3]
put(p, "TextBox 18", "Полезность\nи ценность", size=72)
put(p, "TextBox 19", "БОЛЬ")
put(p, "TextBox 20", "ЦИФРЫ")
put(p, "TextBox 21", "МАСШТАБ")
put(p, "TextBox 22",
    "Живут вместе — тратят вместе. Кто кому должен, считают в Excel или "
    "в переписке: на третьей покупке никто уже не сходится, начинаются споры.")
put(p, "TextBox 23",
    "Взаимозачёт сводит N×(N−1)/2 встречных долгов к N−1 переводу: впятером "
    "это 4 перевода вместо 10. Покупка — одно сообщение или голос, категорию "
    "ставит ИИ.")
put(p, "TextBox 24",
    "Бюджетов сколько угодно: квартира, поездка, семья, общежитие. "
    "Два режима расчётов — общая касса и дележ.")
put(p, "TextBox 25",
    "Работает и без Telegram: вход по ссылке-приглашению или через Яндекс ID.")
brand(p, logo="TextBox 16", date="TextBox 17")

# --------------------------------------------------- 3. технологии и ИИ --
i = s[4]
put(i, "TextBox 27", "Инновационные\nтехнологии", size=66)
put(i, "TextBox 22", "КИЛЛЕР-ФИЧА")
put(i, "TextBox 20", "ИИ В ЯДРЕ")
put(i, "TextBox 21", "ВАЙБ-КОДИНГ")
put(i, "TextBox 25",
    "Один сценарий в трёх местах: чат, мини-аппа и браузер работают с общими "
    "данными. Аналоги требуют, чтобы приложение поставили все участники.")
put(i, "TextBox 26",
    "Расчёт долгов живёт в чате, где люди и так договариваются.")
put(i, "TextBox 23",
    "Категоризация покупок, советы по оптимизации, вежливые напоминания "
    "о долгах и голосовой ввод. Выключить ИИ — продукт станет обычной "
    "таблицей.")
put(i, "TextBox 24",
    "Claude Code: генерация модулей, разбор ошибок, автотесты, вёрстка "
    "мини-аппы. 83 коммита за 4 дня, 4 набора автотестов.")
brand(i, logo="TextBox 18", date="TextBox 19")

# ------------------------------------------------------- 4. архитектура --
a = s[11]
put(a, "TextBox 15", "Архитектура", size=72)
move(a, "TextBox 13", top=3.1)
put(a, "TextBox 13",
    "Бот и веб — один процесс: общая бизнес-логика, одна база, никакой "
    "рассинхронизации между интерфейсами.")
drop(a, "TextBox 14", "Picture 10", "Group 5", "Group 8")
move(a, "TextBox 15", top=1.7)
move(a, "TextBox 13", top=2.9, width=8.0)
brand(a, logo="TextBox 11", date="TextBox 12")
# Ширина подобрана так, чтобы схема целиком поместилась по высоте слайда.
picture(a, SP / "arch.png", left=1.6, top=4.15, width=16.6)

# -------------------------------------------------------- 5. репозиторий --
r = s[9]
put(r, "TextBox 17", "Репозиторий", size=72)
put(r, "TextBox 16",
    f"{REPO}\n\n83 коммита · 6 участников · 4 дня\n"
    "12,6 тыс. строк кода, из них 1,7 тыс. — автотесты\n"
    "README с разбором архитектуры и запуском одной командой")
drop(r, "Picture 13", "Group 5")
brand(r, logo="TextBox 14", date="TextBox 15")
picture(r, SP / "qr.png", left=2.6, top=2.6, width=6.2)

# --------------------------------------------- 6. техническая реализация --
d = s[6]
put(d, "TextBox 25", "Техническая\nреализация", size=66)
put(d, "TextBox 26", "83", size=110)
put(d, "TextBox 27", "4/4", size=110)
put(d, "TextBox 23",
    "коммита за 4 дня, история открыта — видно вклад каждого участника")
put(d, "TextBox 24",
    "набора автотестов проходят на боевом сервере: ядро расчётов, API, "
    "хендлеры бота и разметка сообщений")
put(d, "TextBox 22",
    "Прототип развёрнут и работает: Docker на Debian VPS, HTTPS, бот в "
    "long polling. Проверяемые инварианты — сумма долей равна сумме покупки, "
    "балансы в режиме дележа сходятся в ноль.")
brand(d, logo="TextBox 20", date="TextBox 21")

# ------------------------------------------------------------- 7. UX/UI --
u = s[7]
put(u, "TextBox 18", "Опыт", size=72)
move(u, "TextBox 18", left=1.3, top=1.9, width=5.0)
# Надпись «Опыт» крупная, а раздел раскрывают подписи «было / стало» ниже:
# второй заголовок в этом макете сжимается и наезжает на текст.
drop(u, "TextBox 19", "Group 6", "Group 9", "Group 20")

# Слева — сравнение «было / стало», справа — реальные экраны.
put(u, "TextBox 14", "БЫЛО · 5+ действий")
move(u, "TextBox 14", left=1.3, top=3.6, width=4.4)
put(u, "TextBox 16",
    "Открыть Excel, найти строку, посчитать доли, разнести по участникам, "
    "написать в чат — и надеяться, что никто не ошибся.")
move(u, "TextBox 16", left=1.3, top=4.3, width=4.4)

put(u, "TextBox 15", "СТАЛО · 1 действие")
move(u, "TextBox 15", left=1.3, top=6.9, width=4.4)
put(u, "TextBox 17",
    "«молоко хлеб 850» боту или голосом. Категория, доли и балансы "
    "посчитаны, диаграмма обновлена.")
move(u, "TextBox 17", left=1.3, top=7.6, width=4.4)

brand(u, logo="TextBox 12", date="TextBox 13")
picture(u, SP / "screens.png", left=6.4, top=1.5, width=13.0)

# ----------------------------------------------------------- 8. команда --
c = s[12]
put(c, "TextBox 23", "Команда", size=84)
put(c, "TextBox 22",
    "83 коммита за 4 дня. История открыта — вклад каждого виден в Git."
    + chr(10) * 2
    + "yaskorinov · sk4y3z · 67sanechka67 · Imirukko · diagnezzz · lazexx")
move(c, "TextBox 22", top=6.9, width=5.4)
put(c, "TextBox 24", "YASKORINOV")
put(c, "TextBox 26", "— роль —")
put(c, "TextBox 25", "SK4Y3Z")
put(c, "TextBox 27", "— роль —")
put(c, "TextBox 28", "67SANECHKA67")
put(c, "TextBox 29", "— роль —")
brand(c, logo="TextBox 20", date="TextBox 21")

# Стоковые лица из шаблона — чужие люди; ставим плитки с инициалами.
drop(c, "Group 11", "Group 13", "Group 18")
for name, left in (("ava-Y.png", 7.9), ("ava-S.png", 11.8), ("ava-A.png", 15.8)):
    picture(c, SP / name, left=left, top=3.3, width=2.9)

# ----------------------------------------------------------- 9. спасибо --
f = s[13]
put(f, "TextBox 6", "СПАСИБО", size=130)
move(f, "TextBox 6", top=1.6, height=2.2)
put(f, "TextBox 13", "Вопросы?")
put(f, "TextBox 16", "Кейс 2 · Сбер")
put(f, "TextBox 17", "AI-ассистент общих расходов")
put(f, "TextBox 18", REPO, size=16)
brand(f, logo="TextBox 15", date="TextBox 19")
drop(f, "Group 10", "TextBox 14")
to_front(f, "TextBox 6", "TextBox 13", "TextBox 16", "TextBox 17", "TextBox 18")

keep_only(prs, ORDER)
OUT.parent.mkdir(parents=True, exist_ok=True)
prs.save(OUT)
print(f"готово: {OUT} — {len(Presentation(OUT).slides)} слайдов")
