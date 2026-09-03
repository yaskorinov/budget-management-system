"""Круговые диаграммы расходов -> PNG для Telegram и мини-аппы.

Оформление: кольцевая диаграмма (part-to-whole с первого взгляда), не больше
шести сегментов, у каждого прямой процентный ярлык и строка легенды с суммой,
подписи — чернильными цветами, а не цветом сегмента. Цвета берутся из палитры
категорий (`app/core/categories.py`) и валидированных категориальных слотов.
"""
from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass
from functools import lru_cache

from app.core import categories as cat
from app.core.money import format_money

log = logging.getLogger(__name__)

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#8a8985"

# Категориальные слоты для диаграммы «по людям» (порядок менять нельзя —
# именно он обеспечивает различимость соседних цветов при дальтонизме).
PERSON_COLORS = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#4a3aa7",
    "#e34948",
)
REST_COLOR = "#8a8985"

MAX_SLICES = 6  # больше шести сегментов кольцо уже не читается


@dataclass(slots=True)
class Slice:
    label: str
    value: int  # копейки
    color: str


def fold_slices(items: list[Slice], limit: int = MAX_SLICES, rest_label: str = "Другие") -> list[Slice]:
    """Оставляет топ-N сегментов, остальное сворачивает в один серый."""
    items = [s for s in items if s.value > 0]
    items.sort(key=lambda s: s.value, reverse=True)
    if len(items) <= limit:
        return items
    head, tail = items[: limit - 1], items[limit - 1 :]
    head.append(Slice(rest_label, sum(s.value for s in tail), REST_COLOR))
    return head


def category_slices(rows: list[tuple[str, int]]) -> list[Slice]:
    return fold_slices(
        [Slice(cat.get(code).title, value, cat.get(code).color) for code, value in rows],
        rest_label="Прочее",
    )


def person_slices(rows: list[tuple[object, int]]) -> list[Slice]:
    slices = [
        Slice(getattr(user, "short_name", str(user)), value, PERSON_COLORS[i % len(PERSON_COLORS)])
        for i, (user, value) in enumerate(rows)
    ]
    return fold_slices(slices)


@lru_cache(maxsize=1)
def currency_symbol() -> str:
    """₽ есть не в каждом шрифте — если глифа нет, подписываем «руб.»."""
    try:
        from matplotlib.font_manager import FontProperties, findfont, get_font

        font = get_font(findfont(FontProperties(family="DejaVu Sans")))
        return "₽" if font.get_char_index(0x20BD) else "руб."
    except Exception:  # matplotlib не установлен или шрифт не найден
        return "₽"


def render_donut(
    *,
    title: str,
    subtitle: str,
    slices: list[Slice],
    total_caption: str = "всего",
) -> bytes | None:
    """Рисует кольцевую диаграмму и возвращает PNG. None — если рисовать нечего."""
    slices = [s for s in slices if s.value > 0]
    if not slices:
        return None

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
    except Exception as exc:  # pragma: no cover — окружение без matplotlib
        log.warning("matplotlib недоступен (%s), диаграмма не построена", exc)
        return None

    symbol = currency_symbol()
    total = sum(s.value for s in slices)

    fig = plt.figure(figsize=(9.6, 5.4), dpi=120, facecolor=SURFACE)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.99, bottom=0.01)

    fig.text(0.045, 0.93, title, color=INK_PRIMARY, fontsize=17, fontweight="bold", va="top")
    fig.text(0.045, 0.855, subtitle, color=INK_SECONDARY, fontsize=11.5, va="top")

    ax = fig.add_axes((0.03, 0.05, 0.46, 0.74))
    ax.set_facecolor(SURFACE)

    wedges, _ = ax.pie(
        [s.value for s in slices],
        colors=[s.color for s in slices],
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.36, "edgecolor": SURFACE, "linewidth": 2},
    )
    ax.set(aspect="equal")

    # Прямые ярлыки процентов — только там, где сегмент достаточно широк.
    for wedge, item in zip(wedges, slices):
        share = item.value / total
        if share < 0.045:
            continue
        angle = (wedge.theta2 + wedge.theta1) / 2
        x = 0.82 * math.cos(math.radians(angle))
        y = 0.82 * math.sin(math.radians(angle))
        ax.text(
            x, y, f"{share * 100:.0f}%",
            ha="center", va="center",
            color=INK_PRIMARY, fontsize=10.5, fontweight="bold",
        )

    ax.text(0, 0.12, format_money(total, symbol), ha="center", va="center",
            color=INK_PRIMARY, fontsize=17, fontweight="bold")
    ax.text(0, -0.14, total_caption, ha="center", va="center",
            color=INK_MUTED, fontsize=10.5)

    # Легенда: цветной маркер + название + сумма + доля, текст чернильный.
    legend = fig.add_axes((0.52, 0.05, 0.46, 0.74))
    legend.set_axis_off()
    legend.set_xlim(0, 1)
    legend.set_ylim(0, 1)

    rows = len(slices)
    step = min(0.145, 0.92 / max(rows, 1))
    top = 0.5 + (rows - 1) * step / 2

    for i, item in enumerate(slices):
        y = top - i * step
        legend.add_patch(
            FancyBboxPatch(
                (0.0, y - 0.022),
                0.032,
                0.044,
                boxstyle="round,pad=0,rounding_size=0.012",
                linewidth=0,
                facecolor=item.color,
            )
        )
        legend.text(0.06, y, item.label, ha="left", va="center",
                    color=INK_PRIMARY, fontsize=12)
        legend.text(0.98, y + 0.012, format_money(item.value, symbol), ha="right",
                    va="center", color=INK_PRIMARY, fontsize=12)
        legend.text(0.98, y - 0.028, f"{item.value / total * 100:.1f}%", ha="right",
                    va="center", color=INK_MUTED, fontsize=9.5)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buffer.getvalue()


def render_text_stats(slices: list[Slice], symbol: str = "₽") -> str:
    """Текстовая версия диаграммы — колонки выровнены под моноширинный блок."""
    slices = [item for item in slices if item.value > 0]
    if not slices:
        return "Пока нет расходов за этот период."

    total = sum(item.value for item in slices)
    rows = [
        (
            item.label,
            format_money(item.value, symbol),
            f"{item.value / total * 100:.0f}%",
            "█" * max(1, round(item.value / total * 10)),
        )
        for item in slices
    ]
    label_width = max(len(row[0]) for row in rows)
    money_width = max(len(row[1]) for row in rows)

    lines = [
        f"{label:<{label_width}}  {amount:>{money_width}}  {share:>4}  {bar}"
        for label, amount, share, bar in rows
    ]
    lines.append("")
    lines.append(f"{'Итого':<{label_width}}  {format_money(total, symbol):>{money_width}}")
    return "\n".join(lines)
