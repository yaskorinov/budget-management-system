"""Проверка LLM-категоризации: python scripts/check_llm.py "молоко хлеб 850" """
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from app.config import settings
from app.core.classifier import parse_purchase

SAMPLES = [
    "молоко хлеб яйца 850",
    "туалетка и фейри 450",
    "квартплата за август 6200",
    "подписка нетфликс 799",
    "новый чайник bosch 3500",
    "два билета в кино 1200",
]


async def main() -> None:
    texts = sys.argv[1:] or SAMPLES
    print(f"Провайдер: {settings.llm_provider}")
    print(f"Адрес:     {settings.llm_base_url or chr(8212)}")
    print(f"Модель:    {settings.llm_model}")
    print(f"Голос:     {settings.llm_voice_model}")
    key = "есть" if settings.llm_api_key else "НЕТ"
    print(f"Ключ:      {key}")
    print()

    if not settings.llm_enabled:
        print("LLM выключен (LLM_PROVIDER=off или пустой LLM_API_KEY) — работает словарь.")
        return

    # Пробный запрос: ошибки классификатор глушит и уходит в словарь,
    # поэтому спрашиваем модель напрямую и показываем причину как есть
    from app.core.classifier import chat

    try:
        answer = await chat(
            [{"role": "user", "content": "Ответь одним словом: работает"}],
            max_tokens=20,
        )
        print(f"Ответ модели: {answer.strip()[:60]}")
    except Exception as exc:
        print(f"Модель не ответила: {type(exc).__name__}: {exc}")
        print("Категоризация будет работать по словарю, советы и голос — нет.")
        return

    print()
    for text in texts:
        parsed = await parse_purchase(text)
        amount = f"{parsed.amount / 100:.2f}" if parsed.amount else chr(8212)
        print(
            f"{text:<34} -> {parsed.category:<14} {amount:>10}  "
            f"«{parsed.title}»  [{parsed.source}]"
        )

    if all((await parse_purchase(t)).source == "rules" for t in texts[:1]):
        print()
        print("Категории определил словарь, а не модель — смотрите лог приложения.")


if __name__ == "__main__":
    asyncio.run(main())
