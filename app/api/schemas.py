"""Схемы API. Все суммы — целые копейки."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class TelegramAuthIn(BaseModel):
    init_data: str


class MagicAuthIn(BaseModel):
    token: str


class UserOut(BaseModel):
    id: int
    name: str
    username: str | None = None


class GroupOut(BaseModel):
    id: int
    title: str
    currency: str


class AuthOut(BaseModel):
    token: str
    user: UserOut
    groups: list[GroupOut]
    active_group_id: int | None


class ShareOut(BaseModel):
    user_id: int
    name: str
    amount: int


class OperationOut(BaseModel):
    id: int
    kind: str
    amount: int
    category: str | None
    category_title: str | None
    title: str | None
    author_id: int
    author: str
    occurred_at: dt.datetime
    can_edit: bool
    shares: list[ShareOut]


class OperationIn(BaseModel):
    kind: str = Field(pattern="^(contribution|purchase)$")
    amount: int = Field(gt=0, description="копейки")
    title: str | None = None
    category: str | None = None
    participant_ids: list[int] | None = None
    occurred_at: dt.datetime | None = None


class OperationPatch(BaseModel):
    amount: int | None = Field(default=None, gt=0)
    title: str | None = None
    category: str | None = None
    participant_ids: list[int] | None = None
    occurred_at: dt.datetime | None = None


class BalanceOut(BaseModel):
    user_id: int
    name: str
    contributed: int
    spent: int
    balance: int


class SummaryOut(BaseModel):
    group: GroupOut
    fund_left: int
    total_contributed: int
    total_spent: int
    members: list[BalanceOut]


class SliceOut(BaseModel):
    label: str
    value: int
    color: str


class StatsOut(BaseModel):
    mode: str
    period: str
    period_title: str
    total: int
    slices: list[SliceOut]
    chart_url: str | None = None


class CategorizeIn(BaseModel):
    text: str


class CategorizeOut(BaseModel):
    amount: int | None
    category: str
    category_title: str
    title: str
    source: str


class CategoryOut(BaseModel):
    code: str
    title: str
    emoji: str
    color: str
