"""Отпечатки файлов мини-аппы.

Вебвью Telegram кеширует страницу и её скрипты по адресу, поэтому после
выкладки человек ещё долго видит прежнюю сборку. Отпечаток содержимого в
адресе решает это без чистки кеша: меняется файл — меняется адрес.

Считаем один раз на процесс: файлы внутри контейнера не меняются, а к
каждому запросу страницы хешировать их незачем.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache

from app.config import BASE_DIR

WEB_DIR = BASE_DIR / "app" / "web"


@lru_cache
def asset_version(name: str) -> str:
    """Короткий отпечаток файла. Файла нет — «0», адрес просто без версии."""
    path = WEB_DIR / name
    if not path.is_file():
        return "0"
    return hashlib.md5(path.read_bytes()).hexdigest()[:10]


def versioned_index() -> str:
    """index.html со ссылками вида /app.js?v=<отпечаток>."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("styles.css", "app.js"):
        html = html.replace(f'"/{name}"', f'"/{name}?v={asset_version(name)}"')
    return html
