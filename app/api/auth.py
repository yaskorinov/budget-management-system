"""Аутентификация веба: Telegram WebApp initData и одноразовые ссылки из бота."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import get_session
from app.db.models import User

SESSION_TTL_SECONDS = 30 * 24 * 3600
INIT_DATA_TTL_SECONDS = 24 * 3600

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="expense-bot-session")


def issue_session(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id})


def read_session(token: str) -> int | None:
    try:
        data = _serializer.loads(token, max_age=SESSION_TTL_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return int(data.get("uid", 0)) or None


def verify_init_data(init_data: str) -> dict | None:
    """Проверяет подпись Telegram WebApp initData.

    Схема WebApp: secret = HMAC_SHA256(key="WebAppData", msg=bot_token),
    затем HMAC этим ключом по отсортированной строке полей без hash.
    """
    if not init_data or not settings.bot_token:
        return None

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    auth_date = int(pairs.get("auth_date", "0") or 0)
    if auth_date and time.time() - auth_date > INIT_DATA_TTL_SECONDS:
        return None

    try:
        return json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError:
        return None


async def current_user(
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    user_id = read_session(token) if token else None
    user = await session.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Нужна авторизация"
        )
    return user
