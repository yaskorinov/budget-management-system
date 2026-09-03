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
    print(f"Провайдер: {settings.llm_provider} · модель: {settings.llm_model} · "
          f"ключ: {'есть' if settings.llm_api_key else 'НЕТ'}\n")
    for text in texts:
        parsed = await parse_purchase(text)
        amount = f"{parsed.amount / 100:.2f}" if parsed.amount else "—"
        print(f"{text:<34} -> {parsed.category:<14} {amount:>10}  «{parsed.title}»  [{parsed.source}]")
    if not settings.llm_enabled:
        print("\nLLM выключен (LLM_PROVIDER=off или пустой LLM_API_KEY) — работает словарь.")


if __name__ == "__main__":
    asyncio.run(main())
