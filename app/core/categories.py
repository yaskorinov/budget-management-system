"""Категории расходов: коды, отображение, цвета и словарный фолбэк."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FOOD = "food"
HOUSEHOLD = "household"
UTILITIES = "utilities"
SUBSCRIPTIONS = "subscriptions"
GOODS = "goods"
OTHER = "other"


@dataclass(frozen=True)
class Category:
    code: str
    title: str
    emoji: str
    color: str
    hint: str
    keywords: tuple[str, ...] = field(default=())


# Цвета — слоты 1-5 валидированной категориальной палитры (светлый режим),
# «прочее» намеренно серое: это не отдельная сущность, а остаток.
CATEGORIES: tuple[Category, ...] = (
    Category(
        FOOD, "Продукты питания", "🍎", "#7baeea",
        "еда и всё съедобное из магазина",
        ("продукт", "еда", "магазин", "пятёроч", "пятероч", "перекрёст", "перекрест",
         "магнит", "ашан", "лента", "вкусвилл", "молок", "хлеб", "яйц", "сыр", "мясо",
         "куриц", "рыба", "овощ", "фрукт", "кофе", "чай", "сахар", "масло", "макарон",
         "крупа", "йогурт", "колбас", "вода", "сок", "пиво", "вино", "снек", "доставка еды",
         "самокат", "озон фреш", "яндекс лавка", "лавка", "обед", "ужин", "завтрак"),
    ),
    Category(
        HOUSEHOLD, "Бытовые мелочи", "🧴", "#f09a6b",
        "бытовая химия, гигиена, расходники",
        ("бытов", "химия", "туалетн", "туалетк", "бумаг", "салфет", "мыло", "шампун",
         "порошок", "фейри", "fairy", "средство для", "чистящ", "моющ", "губк", "тряпк",
         "мешки для мусора", "пакет", "зубн", "паста", "батарейк", "лампочк", "освежител",
         "стирал", "кондиционер для бель", "перчатк", "фольг", "плёнк", "пленк"),
    ),
    Category(
        UTILITIES, "Коммуналка", "💡", "#5bc79e",
        "квартплата, свет, вода, газ, интернет, аренда",
        ("коммунал", "квартплат", "квитанц", "жкх", "электр", "свет", "вода",
         "водоснабж", "газ", "отоплен", "интернет", "домофон", "капремонт", "аренда",
         "квартир", "съём", "съем", "консьерж", "вывоз мусора", "мусор"),
    ),
    Category(
        SUBSCRIPTIONS, "Общие подписки", "📺", "#f0c463",
        "стриминги и общие сервисы по подписке",
        ("подписк", "netflix", "нетфликс", "spotify", "спотифай", "яндекс плюс",
         "яндекс.плюс", "плюс", "кинопоиск", "ivi", "иви", "okko", "окко", "amediateka",
         "youtube premium", "ютуб", "apple music", "icloud", "облак", "vpn", "впн",
         "chatgpt", "claude", "midjourney", "steam", "megogo", "premium", "премиум"),
    ),
    Category(
        GOODS, "Общие предметы", "🛋", "#ea9cbe",
        "техника, мебель, посуда и прочее в общее пользование",
        ("телевизор", "чайник", "холодильник", "стирал", "пылесос", "микроволнов",
         "мультиварк", "мебель", "диван", "стол", "стул", "шкаф", "полк", "матрас",
         "посуд", "кастрюл", "сковород", "тарелк", "нож", "лампа", "светильник",
         "штор", "ковёр", "ковер", "подушк", "одеял", "постельн", "утюг", "фен",
         "роутер", "техник", "инструмент", "дрель", "днс", "мвидео", "эльдорадо", "икеа", "ikea"),
    ),
    Category(
        OTHER, "Прочее", "📦", "#8e8d88",
        "всё, что не попало в другие категории",
        (),
    ),
)

BY_CODE: dict[str, Category] = {c.code: c for c in CATEGORIES}
CODES: tuple[str, ...] = tuple(c.code for c in CATEGORIES)


def get(code: str | None) -> Category:
    return BY_CODE.get(code or "", BY_CODE[OTHER])


def label(code: str | None) -> str:
    c = get(code)
    return f"{c.emoji} {c.title}"


def prompt_reference() -> str:
    """Список категорий для системного промпта LLM."""
    return "\n".join(f"- {c.code}: {c.title} — {c.hint}" for c in CATEGORIES)


def guess_by_keywords(text: str) -> str:
    """Словарный фолбэк: побеждает категория с самым длинным совпадением."""
    low = re.sub(r"\s+", " ", (text or "").lower().replace("ё", "ё"))
    best_code, best_len = OTHER, 0
    for category in CATEGORIES:
        for word in category.keywords:
            if word in low and len(word) > best_len:
                best_code, best_len = category.code, len(word)
    return best_code
