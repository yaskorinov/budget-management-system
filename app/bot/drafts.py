"""Черновики операций для inline-режима.

Inline-запрос не знает chat_id и не может ничего записать в базу до того, как
пользователь выберет результат. Поэтому разобранная операция кладётся в
короткоживущий черновик, а запись в базу происходит по подтверждению
(кнопкой или через chosen_inline_result, если включён inline feedback).
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field

TTL_SECONDS = 30 * 60
MAX_DRAFTS = 2000


@dataclass
class Draft:
    id: str
    tg_user_id: int
    group_id: int
    kind: str  # contribution | purchase
    amount: int
    title: str = ""
    category: str = "other"
    category_source: str = "rules"
    raw_text: str = ""
    participant_ids: list[int] | None = None
    operation_id: int | None = None  # заполняется после подтверждения
    created_at: float = field(default_factory=time.monotonic)


_drafts: dict[str, Draft] = {}


def _cleanup() -> None:
    now = time.monotonic()
    stale = [key for key, draft in _drafts.items() if now - draft.created_at > TTL_SECONDS]
    for key in stale:
        _drafts.pop(key, None)
    while len(_drafts) > MAX_DRAFTS:
        _drafts.pop(next(iter(_drafts)), None)


def make_id(tg_user_id: int, payload: str) -> str:
    """Одинаковый запрос одного пользователя -> тот же id.

    Inline-запрос прилетает на каждое нажатие клавиши; без этого черновики
    плодились бы десятками на одну покупку.
    """
    normalized = re.sub(r"\s+", " ", payload.strip().lower())
    digest = hashlib.sha1(f"{tg_user_id}|{normalized}".encode()).hexdigest()
    return digest[:16]


def put(draft: Draft) -> Draft:
    _cleanup()
    existing = _drafts.get(draft.id)
    if existing is not None and existing.operation_id is not None:
        return existing  # уже подтверждён — не перетираем
    _drafts[draft.id] = draft
    return draft


def get(draft_id: str) -> Draft | None:
    _cleanup()
    return _drafts.get(draft_id)


def drop(draft_id: str) -> None:
    _drafts.pop(draft_id, None)
