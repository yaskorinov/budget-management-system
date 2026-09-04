"""Разбор SVG-иконок в путь matplotlib.

Иконки хочется держать вектором: растр на диаграмме мылится, а тянуть ради
конвертации cairosvg (нативная библиотека) или reportlab не хочется. Здесь
разбирается только атрибут d у <path> — этого хватает для иконок.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path as FilePath

_TOKENS = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")
_PATH_D = re.compile(r"<path[^>]*\sd=\"([^\"]+)\"", re.S)


def _numbers(tokens: list[str], index: int, count: int) -> tuple[list[float], int]:
    values = [float(tokens[index + i]) for i in range(count)]
    return values, index + count


def _arc_to_cubics(start, radii, rotation, large_arc, sweep, end):
    """Эллиптическая дуга SVG -> кубические кривые (по 90° максимум)."""
    (x1, y1), (x2, y2) = start, end
    rx, ry = abs(radii[0]), abs(radii[1])
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [("L", [x2, y2])]

    phi = math.radians(rotation)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cos_p * dx + sin_p * dy, -sin_p * dx + cos_p * dy

    scale = (x1p**2) / (rx**2) + (y1p**2) / (ry**2)
    if scale > 1:
        rx, ry = rx * math.sqrt(scale), ry * math.sqrt(scale)

    denominator = rx**2 * y1p**2 + ry**2 * x1p**2
    factor = math.sqrt(max((rx**2 * ry**2 - denominator) / denominator, 0.0))
    if large_arc == sweep:
        factor = -factor
    cxp, cyp = factor * rx * y1p / ry, -factor * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2

    def angle_of(ux, uy):
        return math.atan2(uy, ux)

    theta1 = angle_of((x1p - cxp) / rx, (y1p - cyp) / ry)
    theta2 = angle_of((-x1p - cxp) / rx, (-y1p - cyp) / ry)
    delta = theta2 - theta1
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi

    segments = max(1, math.ceil(abs(delta) / (math.pi / 2)))
    step = delta / segments
    alpha = 4 / 3 * math.tan(step / 4)

    result = []
    theta = theta1
    for _ in range(segments):
        cos1, sin1 = math.cos(theta), math.sin(theta)
        cos2, sin2 = math.cos(theta + step), math.sin(theta + step)

        def point(c, s):
            return (
                cx + rx * c * cos_p - ry * s * sin_p,
                cy + rx * c * sin_p + ry * s * cos_p,
            )

        p1, p2 = point(cos1, sin1), point(cos2, sin2)
        d1 = (rx * -sin1 * cos_p - ry * cos1 * sin_p,
              rx * -sin1 * sin_p + ry * cos1 * cos_p)
        d2 = (rx * -sin2 * cos_p - ry * cos2 * sin_p,
              rx * -sin2 * sin_p + ry * cos2 * cos_p)
        result.append(
            ("C", [p1[0] + alpha * d1[0], p1[1] + alpha * d1[1],
                   p2[0] - alpha * d2[0], p2[1] - alpha * d2[1],
                   p2[0], p2[1]])
        )
        theta += step
    return result


def _parse_path(d: str):
    """Разбирает атрибут d в список сегментов ("M"|"L"|"C", координаты)."""
    tokens = _TOKENS.findall(d)
    segments: list[tuple[str, list[float]]] = []
    index, command = 0, ""
    current = start = (0.0, 0.0)
    last_control = None

    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command in "Zz":
                segments.append(("Z", []))
                current = start
                continue
        relative = command.islower()
        upper = command.upper()

        def shift(values, pairs=True):
            """Относительные координаты -> абсолютные."""
            if not relative:
                return values
            out = list(values)
            for k in range(0, len(out), 2 if pairs else 1):
                out[k] += current[0]
                if pairs:
                    out[k + 1] += current[1]
            return out

        if upper == "M":
            values, index = _numbers(tokens, index, 2)
            point = tuple(shift(values))
            segments.append(("M", list(point)))
            current = start = point
            command = "l" if relative else "L"  # следующие пары — линии
        elif upper == "L":
            values, index = _numbers(tokens, index, 2)
            current = tuple(shift(values))
            segments.append(("L", list(current)))
        elif upper == "H":
            values, index = _numbers(tokens, index, 1)
            x = values[0] + (current[0] if relative else 0)
            current = (x, current[1])
            segments.append(("L", list(current)))
        elif upper == "V":
            values, index = _numbers(tokens, index, 1)
            y = values[0] + (current[1] if relative else 0)
            current = (current[0], y)
            segments.append(("L", list(current)))
        elif upper == "C":
            values, index = _numbers(tokens, index, 6)
            values = shift(values)
            segments.append(("C", values))
            last_control = (values[2], values[3])
            current = (values[4], values[5])
        elif upper == "S":
            values, index = _numbers(tokens, index, 4)
            values = shift(values)
            first = (
                2 * current[0] - last_control[0],
                2 * current[1] - last_control[1],
            ) if last_control else current
            segments.append(("C", [first[0], first[1], *values]))
            last_control = (values[0], values[1])
            current = (values[2], values[3])
        elif upper in ("Q", "T"):
            if upper == "Q":
                values, index = _numbers(tokens, index, 4)
                values = shift(values)
                control, point = (values[0], values[1]), (values[2], values[3])
            else:
                values, index = _numbers(tokens, index, 2)
                values = shift(values)
                control = (
                    2 * current[0] - last_control[0],
                    2 * current[1] - last_control[1],
                ) if last_control else current
                point = (values[0], values[1])
            # Квадратичную поднимаем до кубической: matplotlib рисует обе, но
            # так путь получается однородным
            c1 = (current[0] + 2 / 3 * (control[0] - current[0]),
                  current[1] + 2 / 3 * (control[1] - current[1]))
            c2 = (point[0] + 2 / 3 * (control[0] - point[0]),
                  point[1] + 2 / 3 * (control[1] - point[1]))
            segments.append(("C", [c1[0], c1[1], c2[0], c2[1], point[0], point[1]]))
            last_control = control
            current = point
        elif upper == "A":
            values, index = _numbers(tokens, index, 7)
            end = (values[5], values[6])
            if relative:
                end = (end[0] + current[0], end[1] + current[1])
            for kind, coords in _arc_to_cubics(
                current, (values[0], values[1]), values[2],
                bool(values[3]), bool(values[4]), end,
            ):
                segments.append((kind, coords))
            current = end
        else:  # неизвестная команда — дальше разбирать нечего
            break

        if upper not in ("C", "S", "Q", "T"):
            last_control = None

    return segments


@lru_cache(maxsize=32)
def load_icon(path_str: str):
    """SVG -> путь matplotlib, вписанный в квадрат [-0.5, 0.5] с центром в нуле.

    None — если файла нет или в нём не нашлось ни одного <path>.
    """
    file = FilePath(path_str)
    if not file.exists():
        return None

    try:
        from matplotlib.path import Path as MplPath
    except Exception:  # pragma: no cover — окружение без matplotlib
        return None

    vertices: list[tuple[float, float]] = []
    codes: list[int] = []
    for d in _PATH_D.findall(file.read_text(encoding="utf-8")):
        for kind, coords in _parse_path(d):
            if kind == "M":
                vertices.append((coords[0], coords[1]))
                codes.append(MplPath.MOVETO)
            elif kind == "L":
                vertices.append((coords[0], coords[1]))
                codes.append(MplPath.LINETO)
            elif kind == "C":
                vertices += [
                    (coords[0], coords[1]),
                    (coords[2], coords[3]),
                    (coords[4], coords[5]),
                ]
                codes += [MplPath.CURVE4] * 3
            elif kind == "Z":
                vertices.append((0.0, 0.0))
                codes.append(MplPath.CLOSEPOLY)

    if not vertices:
        return None

    from matplotlib.transforms import Affine2D

    # Границы считаем по самой кривой, а не по опорным точкам: контрольные
    # точки безье выходят за контур, и иконка получилась бы смещённой
    raw = MplPath(vertices, codes)
    box = raw.get_extents()
    scale = 1.0 / max(box.width, box.height, 1e-9)

    # В SVG ось Y смотрит вниз — переворачиваем, иначе иконка вверх ногами
    transform = (
        Affine2D()
        .translate(-(box.x0 + box.x1) / 2, -(box.y0 + box.y1) / 2)
        .scale(scale, -scale)
    )
    return transform.transform_path(raw)
