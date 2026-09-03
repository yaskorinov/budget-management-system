"""Схемы callback_data. Ограничение Telegram — 64 байта, поэтому префиксы короткие."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class MenuCB(CallbackData, prefix="m"):
    action: str  # home | add | buy | stats | ops | group | help | web | cancel


class DraftCB(CallbackData, prefix="d"):
    action: str  # ok | cancel | cat | setcat
    draft_id: str
    value: str = ""


class OpCB(CallbackData, prefix="o"):
    action: str  # card | cat | setcat | amount | title | parts | toggle | save | del
    op_id: int
    value: str = ""


class StatsCB(CallbackData, prefix="s"):
    mode: str  # categories | people
    period: str  # month | prev_month | week | all


class GroupCB(CallbackData, prefix="g"):
    action: str  # pick | list | leave
    group_id: int = 0


class OpsPageCB(CallbackData, prefix="p"):
    scope: str  # mine | all
    offset: int = 0
