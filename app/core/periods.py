"""Периоды для статистики — границы считаются в локальном часовом поясе."""
from __future__ import annotations

import datetime as dt

from app.config import settings

TZ = dt.timezone(dt.timedelta(hours=settings.tz_offset_hours))

PERIODS: dict[str, str] = {
    "month": "текущий месяц",
    "prev_month": "прошлый месяц",
    "week": "последние 7 дней",
    "all": "всё время",
}

ALIASES: dict[str, str] = {
    "месяц": "month",
    "этот месяц": "month",
    "текущий месяц": "month",
    "прошлый месяц": "prev_month",
    "прошлый": "prev_month",
    "неделя": "week",
    "неделю": "week",
    "за неделю": "week",
    "7 дней": "week",
    "всё": "all",
    "все": "all",
    "всё время": "all",
    "всегда": "all",
    "всего": "all",
}


def to_local(moment: dt.datetime) -> dt.datetime:
    """Наивный UTC из базы -> наивное локальное время для показа."""
    return moment.replace(tzinfo=dt.timezone.utc).astimezone(TZ).replace(tzinfo=None)


def to_utc(local: dt.datetime) -> dt.datetime:
    return local.replace(tzinfo=TZ).astimezone(dt.timezone.utc).replace(tzinfo=None)


def normalize(name: str | None) -> str:
    key = (name or "").strip().lower()
    if key in PERIODS:
        return key
    return ALIASES.get(key, "month")


def bounds(name: str | None) -> tuple[dt.datetime | None, dt.datetime | None, str]:
    """Возвращает (начало UTC, конец UTC, человекочитаемое название)."""
    period = normalize(name)
    now_local = dt.datetime.now(TZ).replace(tzinfo=None)
    day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "month":
        start = day.replace(day=1)
        return to_utc(start), None, "текущий месяц"

    if period == "prev_month":
        first = day.replace(day=1)
        prev_end = first
        prev_start = (first - dt.timedelta(days=1)).replace(day=1)
        title = f"{prev_start.strftime('%m.%Y')} (прошлый месяц)"
        return to_utc(prev_start), to_utc(prev_end), title

    if period == "week":
        return to_utc(day - dt.timedelta(days=6)), None, "последние 7 дней"

    return None, None, "всё время"


def format_date(moment: dt.datetime) -> str:
    """Свежие даты читаются словами, старые — числом: «сегодня 21:40»."""
    local = to_local(moment)
    today = dt.datetime.now(TZ).replace(tzinfo=None).date()
    delta = (today - local.date()).days

    if delta == 0:
        return f"сегодня {local:%H:%M}"
    if delta == 1:
        return f"вчера {local:%H:%M}"
    if local.year == today.year:
        return f"{local:%d.%m} в {local:%H:%M}"
    return f"{local:%d.%m.%Y}"
