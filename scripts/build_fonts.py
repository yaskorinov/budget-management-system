"""Сборка веб-версий SF Pro Display для мини-аппы.

Шрифт берём из официального пакета Apple — developer.apple.com/fonts,
файл SF-Pro.dmg. Внутри dmg лежит pkg, внутри pkg — Payload (cpio) с
/Library/Fonts. Распаковать можно любым архиватором, понимающим HFS+
(7-Zip умеет), после чего указать сюда папку с .otf:

    python scripts/build_fonts.py <папка с SF-Pro-Display-*.otf>

Полные начертания весят по 6 МБ каждое — почти всё это письменности,
которых в интерфейсе нет. Оставляем латиницу, кириллицу, типографику и
знаки валют, режем в woff2: выходит около сотни килобайт на вес.

Цифры в интерфейсе выровнены по колонкам (font-variant-numeric), поэтому
набор возможностей OpenType сохраняем вместе с tnum — иначе таблицы сумм
поедут.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fontTools.subset import Options, Subsetter, parse_unicodes
from fontTools.ttLib import TTFont

OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "web" / "fonts"

# Ровно те начертания, что встречаются в стилях. Основной текст набран
# средним: Display рисовался под крупные кегли, и в 15px его обычный вес
# выглядит жидковато. Лишние веса в репозитории ни к чему.
WEIGHTS = {"Medium": 500, "Semibold": 600, "Bold": 700}

UNICODES = (
    "U+0020-007E,U+00A0-00FF,"          # латиница и знаки клавиатуры
    "U+0100-017F,"                      # диакритика европейских языков
    "U+0400-045F,U+0490-0491,"          # кириллица и украинская ґ
    "U+2010-2015,U+2018-201A,U+201C-201E,U+2020-2022,U+2026,U+2030,"
    "U+2039-203A,U+2044,U+2116,"        # типографика и №
    "U+20AC,U+20B4,U+20B8,U+20BD,U+20BE,"   # валюты, включая рубль
    "U+2190-2193,U+2212,U+2215,U+2219,U+221E,U+2248,U+2260,U+2264-2265,"
    "U+25CF,U+2713-2714"                # стрелки, математика, точка, галочка
)

FEATURES = [
    "kern", "liga", "clig", "calt", "ccmp", "locl", "mark", "mkmk",
    "tnum", "lnum", "frac", "case", "salt", "ss01", "ss02", "ss03",
]


def build(src: Path, weight_name: str, weight: int) -> tuple[str, int]:
    font = TTFont(src)
    options = Options()
    options.layout_features = FEATURES
    options.flavor = "woff2"
    options.desubroutinize = True       # CFF после подмножества жмётся лучше
    options.name_IDs = ["*"]
    options.notdef_outline = True
    subsetter = Subsetter(options=options)
    subsetter.populate(unicodes=parse_unicodes(UNICODES))
    subsetter.subset(font)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"sf-pro-display-{weight}.woff2"
    font.flavor = "woff2"
    font.save(out)
    return out.name, out.stat().st_size


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    source = Path(sys.argv[1])
    for name, weight in WEIGHTS.items():
        otf = source / f"SF-Pro-Display-{name}.otf"
        if not otf.is_file():
            sys.exit(f"нет файла {otf}")
        filename, size = build(otf, name, weight)
        print(f"{filename}: {size / 1024:.0f} КБ")


if __name__ == "__main__":
    main()
