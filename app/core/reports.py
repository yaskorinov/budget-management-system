"""Сводные отчёты: срез расходов по категориям или по людям + PNG-диаграмма."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import BASE_DIR
from app.core import charts, periods, service
from app.db.models import Group

CHART_DIR = BASE_DIR / "data" / "charts"

MODES = {"categories": "по категориям", "people": "по людям"}
ALIASES = {
    "категории": "categories",
    "категориям": "categories",
    "категория": "categories",
    "по категориям": "categories",
    "cat": "categories",
    "люди": "people",
    "людям": "people",
    "по людям": "people",
    "человек": "people",
    "people": "people",
}


def normalize_mode(value: str | None) -> str:
    key = (value or "").strip().lower()
    if key in MODES:
        return key
    return ALIASES.get(key, "categories")


@dataclass(slots=True)
class Report:
    group: Group
    mode: str
    period: str
    period_title: str
    slices: list[charts.Slice]

    @property
    def total(self) -> int:
        return sum(item.value for item in self.slices)

    @property
    def title(self) -> str:
        return f"Расходы {MODES[self.mode]}"

    @property
    def is_empty(self) -> bool:
        return not self.slices


async def build(
    session: AsyncSession, *, group: Group, mode: str = "categories", period: str = "month"
) -> Report:
    mode = normalize_mode(mode)
    period = periods.normalize(period)
    since, until, period_title = periods.bounds(period)

    if mode == "categories":
        rows = await service.stats_by_category(
            session, group_id=group.id, since=since, until=until
        )
        slices = charts.category_slices(rows)
    else:
        rows = await service.stats_by_person(
            session, group_id=group.id, since=since, until=until
        )
        slices = charts.person_slices(rows)

    return Report(
        group=group,
        mode=mode,
        period=period,
        period_title=period_title,
        slices=slices,
    )


def render_png(report: Report) -> bytes | None:
    return charts.render_donut(
        title=report.title,
        subtitle=f"{report.group.title} · {report.period_title}",
        slices=report.slices,
    )


def render_text(report: Report) -> str:
    return charts.render_text_stats(report.slices)


def save_png(png: bytes) -> str:
    """Кладёт диаграмму в кэш на диске и возвращает имя файла для URL."""
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(png).hexdigest()[:20] + ".png"
    path = CHART_DIR / name
    if not path.exists():
        path.write_bytes(png)
    return name


def chart_path(name: str) -> Path | None:
    """Безопасно разрешает имя файла из URL в путь внутри кэша."""
    candidate = (CHART_DIR / name).resolve()
    if candidate.parent != CHART_DIR.resolve() or not candidate.is_file():
        return None
    return candidate
