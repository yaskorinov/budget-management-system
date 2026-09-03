"""Разбор и форматирование денежных сумм. Внутри всё в копейках."""
from __future__ import annotations

import re

NBSP = " "

_MULTIPLIERS = {"к": 1000, "k": 1000, "тыс": 1000, "т": 1000}

# Число: 1 234,56 / 1234.5 / 850 — с необязательным множителем "к"/"тыс".
_NUMBER_RE = re.compile(
    r"(?<![\d.,])"
    r"(?P<int>\d{1,3}(?:[  ]\d{3})+|\d+)"
    r"(?:[.,](?P<frac>\d{1,2}))?"
    r"(?P<mult>к|k|тыс\.?|т(?![а-яё]))?"
    r"(?![\d.,])",
    re.IGNORECASE,
)

_CURRENCY_RE = re.compile(r"^\s*(₽|руб\.?|рублей|рубля|р\.|р(?![а-яё]))", re.IGNORECASE)
_UNIT_RE = re.compile(
    r"^\s*(шт\.?|штук\w*|кг|гр?\.?|грамм\w*|л(?![а-яё])|мл|уп\.?|пач\w*|%|"
    r"см|мм|мб|гб|тб|мес\w*|дн\w*|год\w*|лет)",
    re.IGNORECASE,
)


def parse_amount(text: str) -> tuple[int | None, str]:
    """Достаёт сумму из свободного текста.

    Возвращает (копейки | None, текст без суммы). Из нескольких чисел выбирает
    самое похожее на цену: приоритет у числа с валютой рядом, штраф — у числа
    с единицей измерения ("0.5 л"), при равенстве побеждает последнее.
    """
    best: tuple[int, int, re.Match[str]] | None = None  # (score, position, match)

    for m in _NUMBER_RE.finditer(text):
        tail = text[m.end() :]
        score = 0
        if _CURRENCY_RE.match(tail):
            score += 10
        if _UNIT_RE.match(tail):
            score -= 10
        if m.group("mult"):
            score += 3
        if best is None or (score, m.start()) >= (best[0], best[1]):
            best = (score, m.start(), m)

    if best is None or best[0] < 0:
        return None, text.strip()

    m = best[2]
    integer = re.sub(r"[  ]", "", m.group("int"))
    frac = (m.group("frac") or "").ljust(2, "0")
    cents = int(integer) * 100 + int(frac or 0)

    mult = (m.group("mult") or "").lower().rstrip(".")
    if mult:
        cents *= _MULTIPLIERS.get(mult, 1)

    if cents <= 0:
        return None, text.strip()

    tail = text[m.end() :]
    currency = _CURRENCY_RE.match(tail)
    if currency:
        tail = tail[currency.end() :]

    rest = text[: m.start()] + " " + tail
    rest = re.sub(r"(?i)(?<![а-яёa-z])(₽|руб(?:лей|ля)?\.?|р\.)(?![а-яёa-z])", " ", rest)
    rest = re.sub(r"[\s,;–—-]+", " ", rest).strip(" ,;-–—")
    return cents, rest


def to_cents(value: float | int | str) -> int:
    """Приводит сумму в рублях (из формы/API) к копейкам."""
    if isinstance(value, str):
        cents, _ = parse_amount(value)
        if cents is None:
            raise ValueError("Не удалось распознать сумму")
        return cents
    return int(round(float(value) * 100))


def format_money(cents: int, symbol: str = "₽") -> str:
    """1234567 -> '12 345,67 ₽'; целые суммы — без копеек."""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    rubles, kopeks = divmod(cents, 100)
    grouped = f"{rubles:,}".replace(",", NBSP)
    tail = f",{kopeks:02d}" if kopeks else ""
    return f"{sign}{grouped}{tail}{NBSP}{symbol}".strip()


def split_amount(total: int, parts: int) -> list[int]:
    """Делит сумму нацело: остаток в копейках раздаётся первым участникам."""
    if parts <= 0:
        raise ValueError("Нужен хотя бы один участник")
    base, remainder = divmod(total, parts)
    return [base + (1 if i < remainder else 0) for i in range(parts)]
