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


def _short_label(label: str, limit: int = 30) -> str:
    """Слишком длинное имя не даст плашке уместиться в ширину картинки."""
    label = label.strip()
    return label if len(label) <= limit else label[: limit - 1].rstrip() + "…"


def _contrast_ink(hex_color: str) -> str:
    """Чёрный или белый — что читаемее на этом фоне."""
    red, green, blue = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)
    # Контраст с белым против контраста с чёрным
    return INK_PRIMARY if (1.05 / (luminance + 0.05)) < ((luminance + 0.05) / 0.05) else "#ffffff"


# Геометрия диаграммы, в дюймах
FIG_WIDTH = 8.6
DPI = 120
HEADER_H = 1.15  # заголовок и подзаголовок
DONUT_H = 4.6
BOTTOM_H = 0.32
RING_WIDTH = 0.30  # доля радиуса: тоньше кольцо — просторнее в центре под сумму

CHIP_FONT = 13.5
CHIP_PAD = 0.62  # доля от кегля, как понимает boxstyle
CHIP_GAP = 12.0
CHIP_ROW_GAP = 12.0


def _chip_metrics() -> tuple[float, float]:
    """Отступ внутри плашки и высота строки плашек, в пикселях."""
    pad_px = CHIP_PAD * CHIP_FONT * DPI / 72
    line_px = CHIP_FONT * DPI / 72 + 2 * pad_px
    return pad_px, line_px


def _measure_chips(labels: list[str], plt) -> list[float]:
    """Ширины плашек. Их знает только отрисовщик, поэтому меряем на черновике."""
    pad_px, _ = _chip_metrics()
    probe = plt.figure(figsize=(FIG_WIDTH, 1), dpi=DPI)
    texts = [probe.text(0, 0, label, fontsize=CHIP_FONT) for label in labels]
    probe.canvas.draw()
    renderer = probe.canvas.get_renderer()
    widths = [t.get_window_extent(renderer).width + 2 * pad_px for t in texts]
    plt.close(probe)
    return widths


def _pack_rows(widths: list[float], limit: float) -> list[list[int]]:
    """Раскладывает плашки по строкам, пока они влезают в ширину."""
    rows: list[list[int]] = [[]]
    used = 0.0
    for index, width in enumerate(widths):
        extra = width if not rows[-1] else width + CHIP_GAP
        if rows[-1] and used + extra > limit:
            rows.append([index])
            used = width
        else:
            rows[-1].append(index)
            used += extra
    return rows


def _short_label(label: str, limit: int = 30) -> str:
    """Слишком длинное имя не даст плашке уместиться в ширину картинки."""
    label = label.strip()
    return label if len(label) <= limit else label[: limit - 1].rstrip() + "…"


def _contrast_ink(hex_color: str) -> str:
    """Чёрный или белый — что читаемее на этом фоне."""
    red, green, blue = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)
    on_white = (luminance + 0.05) / 0.05
    on_black = 1.05 / (luminance + 0.05)
    return INK_PRIMARY if on_white > on_black else "#ffffff"


def render_donut(
    *,
    title: str,
    subtitle: str,
    slices: list[Slice],
    total_caption: str = "всего",
) -> bytes | None:
    """Кольцевая диаграмма: заголовок сверху по центру, под кольцом — плашки
    категорий их же цветом с суммами. None — если рисовать нечего."""
    slices = [s for s in slices if s.value > 0]
    if not slices:
        return None

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover — окружение без matplotlib
        log.warning("matplotlib недоступен (%s), диаграмма не построена", exc)
        return None

    symbol = currency_symbol()
    total = sum(s.value for s in slices)

    labels = [
        f"{_short_label(item.label)} · {format_money(item.value, symbol)}"
        for item in slices
    ]
    widths = _measure_chips(labels, plt)
    rows = _pack_rows(widths, FIG_WIDTH * DPI * 0.92)

    pad_px, line_px = _chip_metrics()
    row_h_in = (line_px + CHIP_ROW_GAP) / DPI
    height_in = HEADER_H + DONUT_H + len(rows) * row_h_in + BOTTOM_H
    width_px, height_px = FIG_WIDTH * DPI, height_in * DPI

    fig = plt.figure(figsize=(FIG_WIDTH, height_in), dpi=DPI, facecolor=SURFACE)

    fig.text(0.5, 1 - 0.32 / height_in, title, color=INK_PRIMARY, fontsize=23,
             fontweight="bold", ha="center", va="top")
    fig.text(0.5, 1 - 0.72 / height_in, subtitle, color=INK_SECONDARY, fontsize=13,
             ha="center", va="top")

    # Кольцо одного размера при любом числе плашек: бокс задан в дюймах
    donut_bottom = (BOTTOM_H + len(rows) * row_h_in) / height_in
    ax = fig.add_axes(
        (
            (1 - DONUT_H / FIG_WIDTH) / 2,
            donut_bottom,
            DONUT_H / FIG_WIDTH,
            DONUT_H / height_in,
        )
    )
    ax.set_facecolor(SURFACE)

    wedges, _ = ax.pie(
        [s.value for s in slices],
        colors=[s.color for s in slices],
        startangle=90,
        counterclock=False,
        # Заметный зазор между сегментами: кольцо читается как набор отдельных
        # долей, а не как сплошное пятно
        wedgeprops={"width": RING_WIDTH, "edgecolor": SURFACE, "linewidth": 7},
    )
    ax.set(aspect="equal")

    band = 1 - RING_WIDTH / 2  # середина кольца
    for wedge, item in zip(wedges, slices):
        share = item.value / total
        if share < 0.045:  # в узкой доле подпись не читается
            continue
        angle = math.radians((wedge.theta2 + wedge.theta1) / 2)
        ax.text(
            band * math.cos(angle),
            band * math.sin(angle),
            f"{share * 100:.0f}%",
            ha="center",
            va="center",
            color=_contrast_ink(item.color),
            fontsize=13,
            fontweight="bold",
        )

    ax.text(0, 0.09, format_money(total, symbol), ha="center", va="center",
            color=INK_PRIMARY, fontsize=21, fontweight="bold")
    ax.text(0, -0.14, total_caption, ha="center", va="center",
            color=INK_MUTED, fontsize=12.5)

    # Плашки: фон — цвет категории, текст — контрастный к нему
    y_px = (BOTTOM_H + len(rows) * row_h_in) * DPI - line_px / 2
    for row in rows:
        row_width = sum(widths[i] for i in row) + CHIP_GAP * (len(row) - 1)
        x_px = (width_px - row_width) / 2
        for index in row:
            fig.text(
                (x_px + pad_px) / width_px,
                y_px / height_px,
                labels[index],
                color=_contrast_ink(slices[index].color),
                fontsize=CHIP_FONT,
                ha="left",
                va="center",
                bbox={
                    "boxstyle": f"round,pad={CHIP_PAD},rounding_size=0.9",
                    "facecolor": slices[index].color,
                    "edgecolor": "none",
                },
            )
            x_px += widths[index] + CHIP_GAP
        y_px -= line_px + CHIP_ROW_GAP

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
