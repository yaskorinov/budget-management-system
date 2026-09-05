"""Проверка выходного прокси по шагам.

Запуск внутри контейнера — оттуда и ходит бот:
    docker compose exec app python scripts/check_proxy.py

Проверяет отдельно: доступен ли сам прокси, проходит ли через него запрос
к Telegram и к API модели. Так видно, на каком шаге рвётся.
"""
from __future__ import annotations

import asyncio
import socket
import sys
from urllib.parse import urlparse

sys.path.insert(0, ".")

from app.config import proxy_warning, settings

TIMEOUT = 8.0


def line(label: str, verdict: str, hint: str = "") -> None:
    print(f"{label:<34} {verdict}")
    if hint:
        print(f"{'':34} {hint}")


async def tcp_reachable(host: str, port: int) -> tuple[bool, str]:
    """Отдельно от SOCKS: сначала важно, доходит ли пакет вообще."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=TIMEOUT
        )
        writer.close()
        return True, "порт отвечает"
    except asyncio.TimeoutError:
        return False, "таймаут — пакет уходит и пропадает, похоже на фильтр"
    except ConnectionRefusedError:
        return False, "отказ — на этом адресе никто не слушает"
    except socket.gaierror as exc:
        return False, f"имя не разрешается ({exc})"
    except OSError as exc:
        return False, str(exc)


async def through_proxy(url: str, proxy: str) -> tuple[bool, str]:
    try:
        import httpx
    except ImportError:
        return False, "httpx не установлен"

    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=TIMEOUT) as client:
            response = await client.get(url)
        return True, f"HTTP {response.status_code}"
    except ImportError:
        return False, "нет пакета socksio — поставьте httpx[socks]"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def main() -> None:
    print(f"PROXY_URL:     {settings.proxy_url or '—'}")
    print(f"LLM_PROXY_URL: {settings.llm_proxy_url or '—'}")
    print()

    if warning := proxy_warning(settings):
        print(f"⚠ {warning}\n")

    checked: set[str] = set()
    for label, proxy, target in (
        ("Telegram", settings.telegram_proxy, "https://api.telegram.org"),
        ("Модель", settings.llm_proxy, settings.llm_base_url or "https://api.openai.com"),
    ):
        if not proxy:
            line(f"{label}: прокси не задан", "идём напрямую")
            continue

        parsed = urlparse(proxy)
        host, port = parsed.hostname or "", parsed.port or 1080
        if proxy not in checked:
            ok, detail = await tcp_reachable(host, port)
            line(f"Прокси {host}:{port}", "доступен" if ok else "НЕ доступен", detail)
            checked.add(proxy)

        ok, detail = await through_proxy(target, proxy)
        line(f"{label} через прокси", "работает" if ok else "НЕ работает", detail)

    print()
    print("Отказ — посмотрите на хосте ss -lntp | grep порт:")
    print("  прокси на 127.0.0.1 из обычного контейнера недостижим, поднимайте")
    print("  бота оверлеем deploy/docker-compose.proxy.yml (сеть хоста).")
    print("Если порт отвечает, а SOCKS не отвечает — проверьте логин и пароль.")


if __name__ == "__main__":
    asyncio.run(main())
