"""Схемы API. Все суммы — целые копейки."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class TelegramAuthIn(BaseModel):
    init_data: str


class InviteAcceptIn(BaseModel):
    token: str
    name: str | None = None


class InviteInfoOut(BaseModel):
    group_title: str
    mode: str
    inviter: str
    expires_at: dt.datetime


class InviteOut(BaseModel):
    url: str
    expires_at: dt.datetime
    uses: int
    max_uses: int


class LinkOut(BaseModel):
    """Одноразовый код привязки и готовая ссылка на бота."""

    code: str
    url: str


class RedirectOut(BaseModel):
    url: str


class MagicAuthIn(BaseModel):
    token: str


class UserOut(BaseModel):
    id: int
    name: str
    username: str | None = None
    is_guest: bool = False       # вошёл по приглашению и ничем себя не подтвердил
    has_telegram: bool = False
    has_yandex: bool = False


class GroupOut(BaseModel):
    id: int
    title: str
    currency: str
    mode: str = "fund"  # fund — общая касса, split — делим расходы


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
    kind: str  # contribution | purchase | transfer
    amount: int
    category: str | None
    category_title: str | None
    title: str | None
    author_id: int
    author: str
    occurred_at: dt.datetime
    can_edit: bool
    shares: list[ShareOut]
    to_user_id: int | None = None   # получатель перевода
    to_user: str | None = None


class OperationIn(BaseModel):
    kind: str = Field(pattern="^(contribution|purchase|transfer)$")
    amount: int = Field(gt=0, description="копейки")
    title: str | None = None
    category: str | None = None
    participant_ids: list[int] | None = None
    to_user_id: int | None = None  # для перевода: кому вернули долг
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


class DebtOut(BaseModel):
    """Один перевод из плана взаимозачёта."""

    from_user_id: int
    from_name: str
    to_user_id: int
    to_name: str
    amount: int


class SummaryOut(BaseModel):
    group: GroupOut
    mode: str = "fund"
    fund_left: int
    total_contributed: int
    total_spent: int
    members: list[BalanceOut]
    debts: list[DebtOut] = []


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
