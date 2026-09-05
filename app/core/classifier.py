"""Категоризация покупок: LLM с фолбэком на словарь ключевых слов."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from app.config import settings
from app.core import categories as cat
from app.core.money import parse_amount

log = logging.getLogger(__name__)

_MAX_CACHE = 512
_cache: dict[str, "Classification"] = {}

SYSTEM_PROMPT = f"""Ты классифицируешь бытовые покупки в общем бюджете \
(соседи по квартире, семья, друзья, которые скидываются в общий котёл).

Доступные категории:
{cat.prompt_reference()}

Правила:
- Выбирай ровно одну категорию из списка по её коду.
- "other" — только если покупка действительно не подходит ни под одну другую категорию.
- Разовая покупка вещи в общее пользование (техника, мебель, посуда) — это "goods",
  а не "household": в "household" идут расходники и бытовая химия.
- Регулярные платежи за жильё и коммунальные услуги, включая интернет и аренду, — "utilities".
- title: короткое название покупки на русском, 1-4 слова, с заглавной буквы, без суммы и валюты.
- amount_rub: сумма в рублях, если она явно указана в тексте, иначе null.

Отвечай только JSON-объектом."""

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(cat.CODES)},
        "title": {"type": "string"},
        "amount_rub": {"type": ["number", "null"]},
    },
    "required": ["category", "title", "amount_rub"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class Classification:
    category: str
    title: str
    amount: int | None = None  # копейки
    source: str = "rules"  # llm | rules


def _fallback(text: str) -> Classification:
    clean = re.sub(r"\s+", " ", text).strip(" .,;-")
    title = (clean[:1].upper() + clean[1:])[:120] if clean else "Покупка"
    return Classification(cat.guess_by_keywords(text), title, None, "rules")


def _cache_put(key: str, value: Classification) -> None:
    if len(_cache) >= _MAX_CACHE:
        _cache.pop(next(iter(_cache)), None)
    _cache[key] = value


def _extract_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


async def _call_anthropic(text: str) -> dict:
    from anthropic import AsyncAnthropic

    kwargs = {"api_key": settings.llm_api_key, "timeout": settings.llm_timeout_seconds}
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url

    client = AsyncAnthropic(**kwargs)
    try:
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
            output_config={
                "format": {"type": "json_schema", "schema": JSON_SCHEMA},
                "effort": "low",
            },
        )
        payload = next(b.text for b in response.content if b.type == "text")
        return _extract_json(payload)
    finally:
        await client.close()


def _headers() -> dict[str, str]:
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    if "openrouter" in settings.llm_base_url:
        # OpenRouter просит представиться: по этим полям он показывает
        # приложение в статистике ключа
        headers["X-Title"] = "Общий бюджет"
        headers["HTTP-Referer"] = settings.public_base or "https://t.me"
    return headers


async def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 256,
    temperature: float = 0.0,
    json_object: bool = False,
    timeout: float | None = None,
) -> str:
    """Запрос к OpenAI-совместимому эндпоинту. Возвращает текст ответа."""
    import httpx

    base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
    payload: dict = {
        "model": model or settings.llm_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if json_object:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(
        timeout=timeout or settings.llm_timeout_seconds
    ) as client:
        response = await client.post(
            f"{base}/chat/completions", headers=_headers(), json=payload
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


async def _call_openai_compatible(text: str) -> dict:
    answer = await chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        json_object=True,
    )
    return _extract_json(answer)


async def classify(text: str) -> Classification:
    """Определяет категорию, короткое название и (если найдена) сумму."""
    text = (text or "").strip()
    if not text:
        return Classification(cat.OTHER, "Покупка", None, "rules")

    key = re.sub(r"\s+", " ", text.lower())
    if cached := _cache.get(key):
        return cached

    result = _fallback(text)
    if settings.llm_enabled:
        try:
            if settings.llm_provider == "anthropic":
                data = await _call_anthropic(text)
            else:
                data = await _call_openai_compatible(text)

            code = str(data.get("category", "")).strip()
            title = str(data.get("title") or "").strip()
            amount_rub = data.get("amount_rub")
            result = Classification(
                category=code if code in cat.BY_CODE else result.category,
                title=(title[:120] or result.title),
                amount=int(round(float(amount_rub) * 100))
                if isinstance(amount_rub, (int, float))
                else None,
                source="llm",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # сеть, лимиты, кривой ответ — работаем по словарю
            log.warning("LLM-категоризация не удалась (%s), фолбэк на словарь", exc)

    _cache_put(key, result)
    return result


@dataclass(slots=True)
class ParsedPurchase:
    amount: int | None
    category: str
    title: str
    source: str


async def parse_purchase(text: str) -> ParsedPurchase:
    """Полный разбор строки покупки: сумма + категория + название."""
    amount, rest = parse_amount(text)
    description = rest or text
    result = await classify(description)
    return ParsedPurchase(
        amount=amount if amount is not None else result.amount,
        category=result.category,
        title=result.title,
        source=result.source,
    )
