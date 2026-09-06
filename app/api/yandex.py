"""Вход через Яндекс ID: обмен кода на токен и запрос профиля.

Ходим тем же прокси, что и остальной исходящий трафик: контейнер может стоять
там, где напрямую наружу нельзя.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"
INFO_URL = "https://login.yandex.ru/info"

STATE_TTL_SECONDS = 15 * 60
TIMEOUT = httpx.Timeout(15.0)

_state = URLSafeTimedSerializer(settings.secret_key, salt="yandex-oauth-state")


class YandexError(RuntimeError):
    """Яндекс не отдал токен или профиль."""


@dataclass(slots=True)
class YandexProfile:
    id: str
    name: str
    email: str | None
    login: str | None


def pack_state(payload: dict) -> str:
    return _state.dumps(payload)


def read_state(raw: str) -> dict | None:
    """Разбирает state. Подпись защищает от подмены invite и чужой привязки."""
    try:
        data = _state.loads(raw, max_age=STATE_TTL_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return data if isinstance(data, dict) else None


def authorize_url(state: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.yandex_client_id,
            "redirect_uri": settings.yandex_redirect_uri,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


async def exchange(code: str) -> str:
    """Код авторизации → access token."""
    async with httpx.AsyncClient(timeout=TIMEOUT, proxy=settings.proxy_url or None) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.yandex_client_id,
                "client_secret": settings.yandex_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.is_error:
        log.warning("Яндекс не выдал токен: %s %s", response.status_code, response.text[:200])
        raise YandexError("Яндекс не подтвердил вход")

    token = response.json().get("access_token")
    if not token:
        raise YandexError("Яндекс не вернул токен доступа")
    return token


async def profile(access_token: str) -> YandexProfile:
    async with httpx.AsyncClient(timeout=TIMEOUT, proxy=settings.proxy_url or None) as client:
        response = await client.get(
            INFO_URL,
            params={"format": "json"},
            headers={"Authorization": f"OAuth {access_token}"},
        )
    if response.is_error:
        log.warning("Профиль не получен: %s %s", response.status_code, response.text[:200])
        raise YandexError("Не удалось получить профиль Яндекса")

    data = response.json()
    user_id = str(data.get("id") or "")
    if not user_id:
        raise YandexError("Яндекс не вернул идентификатор")

    # Показываем имя, а не логин: в списке участников логин выглядит чужеродно.
    name = (
        (data.get("first_name") or "").strip()
        or (data.get("real_name") or "").split(" ")[0].strip()
        or (data.get("display_name") or "").strip()
        or (data.get("login") or "").strip()
    )
    return YandexProfile(
        id=user_id,
        name=name or "Пользователь",
        email=data.get("default_email") or None,
        login=data.get("login") or None,
    )
