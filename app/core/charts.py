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

from app.config import BASE_DIR
from app.core import categories as cat
from app.core.money import format_money

log = logging.getLogger(__name__)

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
ICON_INK = "#1a1a19"  # иконки внутри сегментов — тёмные, палитра под них пастельная

FONT_DIR = BASE_DIR / "assets" / "fonts"
ICON_DIR = BASE_DIR / "assets" / "icons"
INK_SECONDARY = "#52514e"
INK_MUTED = "#8a8985"

# Категориальные слоты для диаграммы «по людям» (порядок менять нельзя —
# именно он обеспечивает различимость соседних цветов при дальтонизме).
PERSON_COLORS = (
    "#7baeea",
    "#f09a6b",
    "#5bc79e",
    "#f0c463",
    "#ea9cbe",
    "#8fcf8f",
    "#a79be0",
    "#ef9694",
)
REST_COLOR = "#8e8d88"

MAX_SLICES = 6  # больше шести сегментов кольцо уже не читается


@dataclass(slots=True)
class Slice:
    label: str
    value: int  # копейки
    color: str
    icon: str = ""  # имя пиктограммы внутри сегмента; пусто — без иконки


def fold_slices(
    items: list[Slice],
    limit: int = MAX_SLICES,
    rest_label: str = "Другие",
    rest_icon: str = "",
) -> list[Slice]:
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
            Slice(cat.get(code).title, value, cat.get(code).color, icon=cat.get(code).code)
            for code, value in rows
        ],
        rest_label="Прочее",
        rest_icon=cat.OTHER,
    )


def person_slices(rows: list[tuple[object, int]]) -> list[Slice]:
    slices = [
        Slice(getattr(user, "short_name", str(user)), value, PERSON_COLORS[i % len(PERSON_COLORS)])
        for i, (user, value) in enumerate(rows)
    ]
    return fold_slices(slices)


def _icon_shapes(name: str):
    """Простые пиктограммы категорий в квадрате [-0.5, 0.5].

    Рисуем векторами, а не эмодзи: matplotlib цветные эмодзи не умеет, а их
    силуэт на пастельном фоне читается хуже однотонной иконки.

    Каждая фигура — кортеж: ("circle", x, y, r) | ("poly", точки, залить)
    | ("line", точки) | ("rect", x, y, w, h, залить).
    """
    if name == "food":  # сумка с покупками
        return [
            ("poly", [(-0.30, -0.34), (0.30, -0.34), (0.36, 0.16), (-0.36, 0.16)], True),
            ("arc", 0.0, 0.14, 0.17, 10, 170),
        ]
    if name == "household":  # капля
        points = [
            (0.29 * math.cos(a), -0.09 + 0.29 * math.sin(a))
            for a in [math.radians(d) for d in range(-160, 21, 10)]
        ]
        return [("poly", [(0.0, 0.45)] + points, True)]
    if name == "utilities":  # домик: платежи за жильё
        return [
            ("poly", [(-0.42, 0.04), (0.0, 0.40), (0.42, 0.04)], True),
            ("rect", -0.30, -0.36, 0.60, 0.40, True),
            ("hole", -0.09, -0.36, 0.18, 0.22),  # дверь: вырез фоном
        ]
    if name == "subscriptions":  # экран с кнопкой воспроизведения
        return [
            ("rect", -0.42, -0.22, 0.84, 0.56, False),
            ("poly", [(-0.08, -0.05), (-0.08, 0.17), (0.13, 0.06)], True),
            ("line", [(-0.20, -0.38), (0.20, -0.38)]),
        ]
    if name == "goods":  # диван: общие вещи в доме
        return [
            ("rect", -0.32, 0.02, 0.64, 0.22, True),
            ("rect", -0.40, -0.18, 0.80, 0.22, True),
            ("line", [(-0.30, -0.18), (-0.30, -0.34)]),
            ("line", [(0.30, -0.18), (0.30, -0.34)]),
        ]
    if name == "other":  # многоточие
        return [("circle", x, 0.0, 0.09, True) for x in (-0.26, 0.0, 0.26)]
    return []


@lru_cache(maxsize=32)
def _icon_file(name: str):
    """Своя иконка из assets/icons/<категория>.svg, если её туда положили."""
    from app.core.svg_icons import load_icon

    return load_icon(str(ICON_DIR / f"{name}.svg"))


def _draw_icon(ax, name: str, cx: float, cy: float, size: float) -> bool:
    """Рисует пиктограмму в точке (cx, cy). False — если такой иконки нет."""
    custom = _icon_file(name)
    if custom is not None:
        from matplotlib.patches import PathPatch
        from matplotlib.transforms import Affine2D

        placed = Affine2D().scale(size).translate(cx, cy).transform_path(custom)
        ax.add_patch(PathPatch(placed, facecolor=ICON_INK, edgecolor="none", zorder=4))
        return True

    shapes = _icon_shapes(name)
    if not shapes:
        return False

    from matplotlib.lines import Line2D
    from matplotlib.patches import Arc, Circle, Polygon, Rectangle

    # Толщина штриха в пунктах: осевой бокс квадратный, DONUT_H дюймов
    scale_pt = DONUT_H * 72 / (2 * AXIS_LIMIT)
    stroke = max(1.0, size * scale_pt * 0.11)

    def point(u: float, v: float) -> tuple[float, float]:
        return cx + u * size, cy + v * size

    for shape in shapes:
        kind = shape[0]
        if kind == "circle":
            _, u, v, r, filled = shape
            x, y = point(u, v)
            ax.add_artist(
                Circle((x, y), r * size, facecolor=ICON_INK if filled else "none",
                       edgecolor=ICON_INK, linewidth=0 if filled else stroke, zorder=4)
            )
        elif kind == "rect":
            _, u, v, w, h, filled = shape
            x, y = point(u, v)
            ax.add_artist(
                Rectangle((x, y), w * size, h * size,
                          facecolor=ICON_INK if filled else "none",
                          edgecolor=ICON_INK, linewidth=0 if filled else stroke,
                          joinstyle="round", zorder=4)
            )
        elif kind == "poly":
            _, points, filled = shape
            ax.add_artist(
                Polygon([point(u, v) for u, v in points], closed=True,
                        facecolor=ICON_INK if filled else "none",
                        edgecolor=ICON_INK, linewidth=0 if filled else stroke,
                        joinstyle="round", zorder=4)
            )
        elif kind == "hole":  # вырез внутри иконки — заливаем фоном
            _, u, v, w, h = shape
            x, y = point(u, v)
            ax.add_artist(
                Rectangle((x, y), w * size, h * size, facecolor=SURFACE,
                          edgecolor="none", zorder=5)
            )
        elif kind == "arc":
            _, u, v, r, deg1, deg2 = shape
            x, y = point(u, v)
            ax.add_artist(
                Arc((x, y), 2 * r * size, 2 * r * size, theta1=deg1, theta2=deg2,
                    edgecolor=ICON_INK, linewidth=stroke, zorder=4)
            )
        elif kind == "line":
            _, points = shape
            xs, ys = zip(*[point(u, v) for u, v in points])
            ax.add_artist(
                Line2D(xs, ys, color=ICON_INK, linewidth=stroke,
                       solid_capstyle="round", zorder=4)
            )
    return True


@lru_cache(maxsize=1)
def chart_font() -> str:
    """Подключает Inter из assets/fonts. Нет файлов — остаётся шрифт по умолчанию."""
    try:
        from matplotlib import font_manager

        added = False
        for path in sorted(FONT_DIR.glob("*.ttf")):
            font_manager.fontManager.addfont(str(path))
            added = True
        return "Inter" if added else "DejaVu Sans"
    except Exception as exc:  # pragma: no cover — окружение без matplotlib
        log.warning("Шрифт Inter не подключён (%s)", exc)
        return "DejaVu Sans"


@lru_cache(maxsize=1)
def currency_symbol() -> str:
    """₽ есть не в каждом шрифте — если глифа нет, подписываем «руб.»."""
    try:
        from matplotlib.font_manager import FontProperties, findfont, get_font

        font = get_font(findfont(FontProperties(family=chart_font())))
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
DONUT_H = 5.4  # больше бокс — кольцо не мельчает от полей под подписи
BOTTOM_H = 0.32
RING_WIDTH = 0.38  # толщина кольца в долях радиуса
AXIS_LIMIT = 1.22  # поле снаружи кольца под подписи процентов
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

    Обычный клин matplotlib рисуется острыми углами, а толстая линия с круглым
    концом превращает узкую долю в капсулу. Поэтому обводим клин сами: две дуги,
    два радиальных торца и четыре скругления.

    Радиусы скругления у внешнего и внутреннего краёв разные: угловой срез
    должен получиться одинаковым, иначе торец выходит косым. Для узкой доли
    скругление уменьшается, чтобы она осталась полоской, а не превратилась
    в кружок. Углы в радианах, start > end (идём по часовой стрелке).
    """
    span = start - end
    if span <= 0:
        return None

    # Скругление ограничено и толщиной кольца, и шириной самой доли
    by_span = math.sin(min(span * 0.45, math.pi / 2))
    corner_out = min(
        corner,
        (outer - inner) / 2 * 0.9,
        by_span * outer / (1 + by_span),
    )
    if corner_out <= 0:
        return None

    sine = corner_out / (outer - corner_out)  # синус углового среза
    if sine >= 1:
        return None
    shift = math.asin(sine)
    corner_in = min(sine * inner / (1 - sine), (outer - inner) / 2 * 0.9)

    outer_c, inner_c = outer - corner_out, inner + corner_in
    outer_foot = math.sqrt(max(outer_c**2 - corner_out**2, 0.0))
    inner_foot = math.sqrt(max(inner_c**2 - corner_in**2, 0.0))

    def polar(radius: float, angle: float) -> tuple[float, float]:
        return radius * math.cos(angle), radius * math.sin(angle)

    def fillet(center, radius, from_point, to_point):
        return _arc_points(
            numpy, center, radius,
            math.atan2(from_point[1] - center[1], from_point[0] - center[0]),
            math.atan2(to_point[1] - center[1], to_point[0] - center[0]),
            steps=12, short=True,
        )

    a_out, b_out = start - shift, end + shift  # где дуги касаются скруглений
    a_in, b_in = start - shift, end + shift
    steps = max(10, int(math.degrees(span)))

    points: list[tuple[float, float]] = []
    points += _arc_points(numpy, (0, 0), outer, a_out, b_out, steps)

    end_out, end_in = polar(outer_foot, end), polar(inner_foot, end)
    points += fillet(polar(outer_c, b_out), corner_out, polar(outer, b_out), end_out)
    points += fillet(polar(inner_c, b_in), corner_in, end_in, polar(inner, b_in))

    points += _arc_points(numpy, (0, 0), inner, b_in, a_in, steps)

    start_out, start_in = polar(outer_foot, start), polar(inner_foot, start)
    points += fillet(polar(inner_c, a_in), corner_in, polar(inner, a_in), start_in)
    points += fillet(polar(outer_c, a_out), corner_out, start_out, polar(outer, a_out))

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
            if outline is not None:
                ax.add_patch(
                    Polygon(outline, closed=True, facecolor=item.color,
                            edgecolor="none", zorder=2)
                )

        _draw_segment_marks(ax, item, start, end, band, total)
        start = end


def _draw_segment_marks(
    ax, item: Slice, start: float, end: float, band: float, total: int
) -> None:
    """Иконка внутри сегмента и процент снаружи кольца."""
    share = item.value / total
    middle = math.radians((start + end) / 2)
    x, y = math.cos(middle), math.sin(middle)

    if item.icon and share >= 0.07:  # в узкой доле иконка не читается
        _draw_icon(ax, item.icon, band * x, band * y, RING_WIDTH * 0.52)

    if share < 0.03:  # подпись такой доли всё равно сольётся с соседней
        return

    # Якорь по направлению: подпись растёт от кольца наружу, а не поперёк него.
    # С якорем по центру половина текста заходила на сегмент справа и слева,
    # а сверху и снизу, наоборот, отходила далеко.
    edge = 0.12
    ax.text(
        1.03 * x, 1.03 * y, f"{share * 100:.0f}%",
        ha="left" if x > edge else ("right" if x < -edge else "center"),
        va="bottom" if y > edge else ("top" if y < -edge else "center"),
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

    matplotlib.rcParams["font.family"] = chart_font()

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

    ax.text(0, 0.055, format_money(total, symbol), ha="center", va="center",
            color=INK_PRIMARY, fontsize=19, fontweight="bold")
    ax.text(0, -0.10, total_caption, ha="center", va="center",
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
