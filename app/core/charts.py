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
from pathlib import Path

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
    icon: str = ""  # эмодзи внутри сегмента; пусто — нарисуем первую букву


def fold_slices(items: list[Slice], limit: int = MAX_SLICES, rest_label: str = "Другие") -> list[Slice]:
    """Оставляет топ-N сегментов, остальное сворачивает в один серый."""
    items = [s for s in items if s.value > 0]
    items.sort(key=lambda s: s.value, reverse=True)
    if len(items) <= limit:
        return items
    head, tail = items[: limit - 1], items[limit - 1 :]
    head.append(
        Slice(rest_label, sum(s.value for s in tail), REST_COLOR, icon="📦")
    )
    return head


def category_slices(rows: list[tuple[str, int]]) -> list[Slice]:
    return fold_slices(
        [
            Slice(cat.get(code).title, value, cat.get(code).color, icon=cat.get(code).emoji)
            for code, value in rows
        ],
        rest_label="Прочее",
    )


def person_slices(rows: list[tuple[object, int]]) -> list[Slice]:
    slices = [
        Slice(getattr(user, "short_name", str(user)), value, PERSON_COLORS[i % len(PERSON_COLORS)])
        for i, (user, value) in enumerate(rows)
    ]
    return fold_slices(slices)


EMOJI_FONTS = (
    "C:/Windows/Fonts/seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
)


@lru_cache(maxsize=1)
def emoji_font_path() -> str | None:
    """Шрифт с эмодзи. matplotlib их не рисует — иконки готовим Pillow-ом."""
    for path in EMOJI_FONTS:
        if Path(path).exists():
            return path
    log.info("Шрифт с эмодзи не найден: в сегментах будут буквы вместо иконок")
    return None


@lru_cache(maxsize=64)
def _icon_image(char: str, size: int = 128):
    """Растрит эмодзи в RGBA. None — если шрифта нет или глифа в нём нет."""
    path = emoji_font_path()
    if not path:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont

        try:
            font = ImageFont.truetype(path, size)
            scale = 1
        except OSError:
            # У цветных шрифтов вроде NotoColorEmoji один фиксированный размер
            font = ImageFont.truetype(path, 109)
            scale = size / 109

        canvas = int(size / scale) if scale != 1 else size
        image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        ImageDraw.Draw(image).text(
            (canvas / 2, canvas / 2), char, font=font, anchor="mm", embedded_color=True
        )
        if scale != 1:
            image = image.resize((size, size), Image.LANCZOS)

        if not image.getbbox():  # глифа в шрифте нет — вышел пустой квадрат
            return None
        import numpy

        return numpy.asarray(image)
    except Exception as exc:  # pragma: no cover — окружение без Pillow/шрифта
        log.warning("Не удалось нарисовать иконку %s (%s)", char, exc)
        return None


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
RING_WIDTH = 0.38  # толщина кольца в долях радиуса
AXIS_LIMIT = 1.22  # запас под подписи процентов снаружи кольца
SEGMENT_GAP_DEG = 3.0  # зазор между сегментами

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


def _arc_points(numpy, center, radius, start_angle, end_angle, steps=10, short=False):
    """Точки дуги вокруг произвольного центра.

    short=True — идти кратчайшим путём: у скруглений угла разница углов должна
    браться по модулю меньше π, иначе дуга заворачивает в обратную сторону и
    вместо скругления получается крючок.
    """
    if short:
        delta = (end_angle - start_angle + math.pi) % (2 * math.pi) - math.pi
        end_angle = start_angle + delta
    angles = numpy.linspace(start_angle, end_angle, steps)
    return [
        (center[0] + radius * math.cos(a), center[1] + radius * math.sin(a))
        for a in angles
    ]


def _rounded_wedge(numpy, start: float, end: float, inner: float, outer: float, corner: float):
    """Контур сегмента кольца со скруглёнными углами.

    Обычный клин matplotlib рисуется острыми углами, а круглый конец линии
    превращает узкий сегмент в «капсулу». Поэтому обводим клин вручную: две
    дуги, два радиальных отреза и четыре скругления между ними.

    Углы в радианах, start > end (идём по часовой стрелке).
    """
    # Скругление не должно съесть сегмент целиком
    corner = min(corner, (outer - inner) / 2 * 0.9)
    outer_c, inner_c = outer - corner, inner + corner
    if outer_c <= 0 or inner_c <= 0:
        return None

    # На сколько центр скругления отступает от радиального края
    outer_shift = math.asin(min(1.0, corner / outer_c))
    inner_shift = math.asin(min(1.0, corner / inner_c))
    if start - end <= 2 * max(outer_shift, inner_shift):
        return None  # сегмент уже, чем два скругления

    # Точки касания на радиальных краях
    outer_foot = math.sqrt(max(outer_c**2 - corner**2, 0.0))
    inner_foot = math.sqrt(max(inner_c**2 - corner**2, 0.0))

    def polar(radius, angle):
        return (radius * math.cos(angle), radius * math.sin(angle))

    points: list[tuple[float, float]] = []

    # Внешняя дуга: от начала сегмента к концу
    a_out, b_out = start - outer_shift, end + outer_shift
    points += _arc_points(numpy, (0, 0), outer, a_out, b_out, 24)

    # Скругление у внешнего края в конце сегмента
    c2 = polar(outer_c, b_out)
    points += _arc_points(
        numpy, c2, corner,
        math.atan2(outer * math.sin(b_out) - c2[1], outer * math.cos(b_out) - c2[0]),
        math.atan2(outer_foot * math.sin(end) - c2[1], outer_foot * math.cos(end) - c2[0]),
        short=True,
    )

    # Скругление у внутреннего края в конце сегмента
    c3 = polar(inner_c, end + inner_shift)
    points += _arc_points(
        numpy, c3, corner,
        math.atan2(inner_foot * math.sin(end) - c3[1], inner_foot * math.cos(end) - c3[0]),
        math.atan2(inner * math.sin(end + inner_shift) - c3[1],
                   inner * math.cos(end + inner_shift) - c3[0]),
        short=True,
    )

    # Внутренняя дуга обратно
    a_in, b_in = end + inner_shift, start - inner_shift
    points += _arc_points(numpy, (0, 0), inner, a_in, b_in, 24)

    # Скругление у внутреннего края в начале сегмента
    c4 = polar(inner_c, b_in)
    points += _arc_points(
        numpy, c4, corner,
        math.atan2(inner * math.sin(b_in) - c4[1], inner * math.cos(b_in) - c4[0]),
        math.atan2(inner_foot * math.sin(start) - c4[1], inner_foot * math.cos(start) - c4[0]),
        short=True,
    )

    # Скругление у внешнего края в начале сегмента
    c1 = polar(outer_c, a_out)
    points += _arc_points(
        numpy, c1, corner,
        math.atan2(outer_foot * math.sin(start) - c1[1], outer_foot * math.cos(start) - c1[0]),
        math.atan2(outer * math.sin(a_out) - c1[1], outer * math.cos(a_out) - c1[0]),
        short=True,
    )

    return points


def _draw_ring(ax, slices: list[Slice], total: int) -> None:
    """Кольцо из толстых сегментов со скруглёнными углами."""
    import numpy
    from matplotlib.patches import Circle, Polygon

    outer, inner = 1.0, 1.0 - RING_WIDTH
    band = (outer + inner) / 2
    corner = RING_WIDTH * 0.20  # мягкое скругление; сильнее — короткие доли превращаются в кляксы

    ax.set_xlim(-AXIS_LIMIT, AXIS_LIMIT)
    ax.set_ylim(-AXIS_LIMIT, AXIS_LIMIT)
    ax.set_axis_off()

    single = len(slices) == 1
    start = 90.0
    for item in slices:
        span = item.value / total * 360
        end = start - span  # по часовой стрелке

        if single:
            ax.add_artist(
                Circle(
                    (0, 0), band, fill=False, edgecolor=item.color, zorder=2,
                    linewidth=RING_WIDTH * DONUT_H * 72 / (2 * AXIS_LIMIT),
                )
            )
        else:
            gap = SEGMENT_GAP_DEG / 2
            outline = _rounded_wedge(
                numpy,
                math.radians(start - gap),
                math.radians(end + gap),
                inner,
                outer,
                corner,
            )
            if outline is None:
                # Слишком узкая доля: рисуем её кружком, иначе она пропадёт
                middle = math.radians((start + end) / 2)
                ax.add_artist(
                    Circle(
                        (band * math.cos(middle), band * math.sin(middle)),
                        RING_WIDTH / 2 * 0.8,
                        facecolor=item.color, edgecolor="none", zorder=2,
                    )
                )
            else:
                ax.add_patch(
                    Polygon(outline, closed=True, facecolor=item.color,
                            edgecolor="none", zorder=2)
                )

        _draw_segment_marks(ax, item, start, end, band, total)
        start = end


def _draw_segment_marks(ax, item: Slice, start: float, end: float, band: float, total: int) -> None:
    """Иконка внутри сегмента и процент снаружи кольца."""
    share = item.value / total
    middle = math.radians((start + end) / 2)
    x, y = math.cos(middle), math.sin(middle)

    # Иконка помещается, только если сегмент не совсем узкий
    if share >= 0.07:
        icon = _icon_image(item.icon) if item.icon else None
        if icon is not None:
            from matplotlib.offsetbox import AnnotationBbox, OffsetImage

            band_px = RING_WIDTH * DONUT_H * DPI / (2 * AXIS_LIMIT)
            target_px = band_px * 0.52
            box = OffsetImage(icon, zoom=target_px * 72 / (len(icon) * DPI))
            ax.add_artist(
                AnnotationBbox(box, (band * x, band * y), frameon=False, zorder=3)
            )
        else:
            ax.text(
                band * x, band * y, item.label[:1].upper(),
                ha="center", va="center", zorder=3,
                color=_contrast_ink(item.color), fontsize=17, fontweight="bold",
            )

    if share >= 0.03:
        outside = 1 + 0.09
        ax.text(
            outside * x, outside * y, f"{share * 100:.0f}%",
            ha="center", va="center",
            color=INK_SECONDARY, fontsize=12.5, fontweight="bold",
        )


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

    _draw_ring(ax, slices, total)

    ax.text(0, 0.09, format_money(total, symbol), ha="center", va="center",
            color=INK_PRIMARY, fontsize=19, fontweight="bold")
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
