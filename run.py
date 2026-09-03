"""Точка входа: один процесс, внутри бот и веб-сервер."""
from __future__ import annotations

import logging

import uvicorn

from app.config import settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # Один воркер обязателен: бот должен работать ровно в одном процессе.
    uvicorn.run(
        "app.api.app:app",
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
