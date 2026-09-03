"""Вся бизнес-логика общего котла. Хендлеры бота и API — тонкие обёртки над ней."""
from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass, field

from sqlalchemy import Select, func, inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.money import split_amount
from app.db.base import utcnow
from app.db.models import (
    CONTRIBUTION,
    PURCHASE,
    Group,
    Membership,
    Operation,
    OperationShare,
    User,
    WebLoginToken,
)


class ServiceError(Exception):
    """Ожидаемая ошибка бизнес-логики — текст показываем пользователю."""


# --------------------------------------------------------------------------- #
#  Пользователи и группы
# --------------------------------------------------------------------------- #


async def get_or_create_user(
    session: AsyncSession,
    *,
    tg_user_id: int,
    first_name: str = "",
    last_name: str | None = None,
    username: str | None = None,
) -> User:
    user = await session.scalar(select(User).where(User.tg_user_id == tg_user_id))
    if user is None:
        user = User(
            tg_user_id=tg_user_id,
            first_name=first_name or "",
            last_name=last_name,
            username=username,
        )
        session.add(user)
        await session.flush()
        return user

    changed = False
    for field_name, value in (
        ("first_name", first_name or user.first_name),
        ("last_name", last_name),
        ("username", username),
    ):
        if value is not None and getattr(user, field_name) != value:
            setattr(user, field_name, value)
            changed = True
    if changed:
        await session.flush()
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_or_create_group_for_chat(
    session: AsyncSession, *, tg_chat_id: int, title: str
) -> Group:
    group = await session.scalar(select(Group).where(Group.tg_chat_id == tg_chat_id))
    if group is None:
        group = Group(tg_chat_id=tg_chat_id, title=title[:255] or "Общий бюджет")
        session.add(group)
        await session.flush()
    elif title and group.title != title[:255]:
        group.title = title[:255]
    return group


async def create_group(session: AsyncSession, *, title: str, owner: User) -> Group:
    group = Group(title=(title or "Общий бюджет")[:255])
    session.add(group)
    await session.flush()
    await ensure_member(session, group_id=group.id, user_id=owner.id, is_admin=True)
    if owner.active_group_id is None:
        owner.active_group_id = group.id
    await session.flush()
    return group


async def ensure_member(
    session: AsyncSession, *, group_id: int, user_id: int, is_admin: bool = False
) -> Membership:
    membership = await session.scalar(
        select(Membership).where(
            Membership.group_id == group_id, Membership.user_id == user_id
        )
    )
    if membership is None:
        membership = Membership(group_id=group_id, user_id=user_id, is_admin=is_admin)
        session.add(membership)
        await session.flush()
    elif not membership.is_active:
        membership.is_active = True
        await session.flush()
    return membership


async def leave_group(session: AsyncSession, *, group_id: int, user_id: int) -> None:
    membership = await session.scalar(
        select(Membership).where(
            Membership.group_id == group_id, Membership.user_id == user_id
        )
    )
    if membership:
        membership.is_active = False
        await session.flush()


async def group_members(session: AsyncSession, group_id: int) -> list[User]:
    rows = await session.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.group_id == group_id, Membership.is_active.is_(True))
        .order_by(Membership.joined_at)
    )
    return list(rows)


async def user_groups(session: AsyncSession, user_id: int) -> list[Group]:
    rows = await session.scalars(
        select(Group)
        .join(Membership, Membership.group_id == Group.id)
        .where(Membership.user_id == user_id, Membership.is_active.is_(True))
        .order_by(Group.created_at)
    )
    return list(rows)


async def is_member(session: AsyncSession, *, group_id: int, user_id: int) -> bool:
    return bool(
        await session.scalar(
            select(Membership.id).where(
                Membership.group_id == group_id,
                Membership.user_id == user_id,
                Membership.is_active.is_(True),
            )
        )
    )


async def resolve_active_group(session: AsyncSession, user: User) -> Group | None:
    """Активная группа пользователя для лички/inline. Чинит битую ссылку сама."""
    if user.active_group_id:
        group = await session.get(Group, user.active_group_id)
        if group and await is_member(session, group_id=group.id, user_id=user.id):
            return group

    groups = await user_groups(session, user.id)
    user.active_group_id = groups[0].id if groups else None
    await session.flush()
    return groups[0] if groups else None


async def set_active_group(session: AsyncSession, user: User, group_id: int) -> Group:
    group = await session.get(Group, group_id)
    if group is None or not await is_member(session, group_id=group_id, user_id=user.id):
        raise ServiceError("Вы не состоите в этой группе")
    user.active_group_id = group_id
    await session.flush()
    return group


# --------------------------------------------------------------------------- #
#  Операции
# --------------------------------------------------------------------------- #


async def _set_shares(
    session: AsyncSession, operation: Operation, participant_ids: list[int]
) -> None:
    """Пересобирает доли участников.

    Связи (`share.user`) заполняются здесь же: в async SQLAlchemy обращение к
    незагруженному атрибуту приводит к ошибке, а карточки операций читают имена.
    """
    unique_ids = list(dict.fromkeys(participant_ids))
    if not unique_ids:
        raise ServiceError("Нужен хотя бы один участник покупки")

    users = {
        user.id: user
        for user in await session.scalars(select(User).where(User.id.in_(unique_ids)))
    }
    missing = [user_id for user_id in unique_ids if user_id not in users]
    if missing:
        raise ServiceError("Среди участников есть неизвестный пользователь")

    if "shares" in sa_inspect(operation).unloaded:
        # Только что созданная операция: долей ещё нет, и обращение к коллекции
        # вызвало бы ленивую загрузку — в async-режиме это ошибка.
        set_committed_value(operation, "shares", [])
    else:
        for old in list(operation.shares):
            await session.delete(old)
        await session.flush()

    shares = [
        OperationShare(
            operation_id=operation.id, user_id=user_id, amount=amount, user=users[user_id]
        )
        for user_id, amount in zip(unique_ids, split_amount(operation.amount, len(unique_ids)))
    ]
    session.add_all(shares)
    await session.flush()
    set_committed_value(operation, "shares", shares)


async def add_contribution(
    session: AsyncSession,
    *,
    group_id: int,
    author_id: int,
    amount: int,
    source: str = "dm",
    occurred_at: dt.datetime | None = None,
    comment: str | None = None,
) -> Operation:
    if amount <= 0:
        raise ServiceError("Сумма должна быть больше нуля")

    author = await session.get(User, author_id)
    if author is None:
        raise ServiceError("Автор операции не найден")

    operation = Operation(
        group_id=group_id,
        author=author,
        kind=CONTRIBUTION,
        amount=amount,
        title=(comment or None),
        source=source,
        occurred_at=occurred_at or utcnow(),
    )
    session.add(operation)
    await session.flush()
    # У взноса долей нет — инициализируем коллекцию, иначе обращение к ней
    # приведёт к ленивой загрузке (в async-режиме это ошибка).
    set_committed_value(operation, "shares", [])
    return operation


async def add_purchase(
    session: AsyncSession,
    *,
    group_id: int,
    author_id: int,
    amount: int,
    category: str,
    title: str,
    participant_ids: list[int] | None = None,
    source: str = "dm",
    category_source: str = "llm",
    raw_text: str | None = None,
    occurred_at: dt.datetime | None = None,
) -> Operation:
    if amount <= 0:
        raise ServiceError("Сумма должна быть больше нуля")

    if not participant_ids:
        members = await group_members(session, group_id)
        participant_ids = [m.id for m in members] or [author_id]

    author = await session.get(User, author_id)
    if author is None:
        raise ServiceError("Автор операции не найден")

    operation = Operation(
        group_id=group_id,
        author=author,
        kind=PURCHASE,
        amount=amount,
        category=category,
        title=title[:255],
        raw_text=(raw_text or None),
        category_source=category_source,
        source=source,
        occurred_at=occurred_at or utcnow(),
    )
    session.add(operation)
    await session.flush()
    await _set_shares(session, operation, participant_ids)
    return operation


async def get_operation(session: AsyncSession, operation_id: int) -> Operation | None:
    operation = await session.get(Operation, operation_id)
    if operation is None or operation.deleted_at is not None:
        return None
    return operation


async def edit_operation(
    session: AsyncSession,
    operation: Operation,
    *,
    amount: int | None = None,
    category: str | None = None,
    title: str | None = None,
    participant_ids: list[int] | None = None,
    occurred_at: dt.datetime | None = None,
) -> Operation:
    if amount is not None:
        if amount <= 0:
            raise ServiceError("Сумма должна быть больше нуля")
        operation.amount = amount
    if category is not None and operation.is_purchase:
        operation.category = category
        operation.category_source = "manual"
    if title is not None:
        operation.title = title[:255]
    if occurred_at is not None:
        operation.occurred_at = occurred_at

    await session.flush()

    if operation.is_purchase and (participant_ids is not None or amount is not None):
        ids = participant_ids or [share.user_id for share in operation.shares]
        await _set_shares(session, operation, ids)

    return operation


async def delete_operation(session: AsyncSession, operation: Operation) -> None:
    operation.deleted_at = utcnow()
    await session.flush()


async def can_manage(session: AsyncSession, operation: Operation, user: User) -> bool:
    """Править операцию может её автор или админ группы."""
    if operation.author_id == user.id:
        return True
    membership = await session.scalar(
        select(Membership).where(
            Membership.group_id == operation.group_id,
            Membership.user_id == user.id,
            Membership.is_active.is_(True),
        )
    )
    return bool(membership and membership.is_admin)


def _period_filter(
    stmt: Select, since: dt.datetime | None, until: dt.datetime | None
) -> Select:
    if since is not None:
        stmt = stmt.where(Operation.occurred_at >= since)
    if until is not None:
        stmt = stmt.where(Operation.occurred_at < until)
    return stmt


async def list_operations(
    session: AsyncSession,
    *,
    group_id: int,
    author_id: int | None = None,
    kind: str | None = None,
    limit: int = 10,
    offset: int = 0,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> list[Operation]:
    stmt = select(Operation).where(
        Operation.group_id == group_id, Operation.deleted_at.is_(None)
    )
    if author_id is not None:
        stmt = stmt.where(Operation.author_id == author_id)
    if kind is not None:
        stmt = stmt.where(Operation.kind == kind)
    stmt = _period_filter(stmt, since, until)
    stmt = stmt.order_by(Operation.occurred_at.desc(), Operation.id.desc())
    return list(await session.scalars(stmt.limit(limit).offset(offset)))


async def count_operations(
    session: AsyncSession,
    *,
    group_id: int,
    author_id: int | None = None,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> int:
    stmt = select(func.count(Operation.id)).where(
        Operation.group_id == group_id, Operation.deleted_at.is_(None)
    )
    if author_id is not None:
        stmt = stmt.where(Operation.author_id == author_id)
    stmt = _period_filter(stmt, since, until)
    return int(await session.scalar(stmt) or 0)


# --------------------------------------------------------------------------- #
#  Балансы и статистика
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class MemberBalance:
    user: User
    contributed: int = 0
    spent: int = 0

    @property
    def balance(self) -> int:
        """Больше нуля — есть запас в фонде; меньше — нужно доложить."""
        return self.contributed - self.spent


@dataclass(slots=True)
class GroupSummary:
    group: Group
    period_title: str
    total_contributed: int = 0
    total_spent: int = 0
    members: list[MemberBalance] = field(default_factory=list)

    @property
    def fund_left(self) -> int:
        return self.total_contributed - self.total_spent


async def summary(
    session: AsyncSession,
    *,
    group: Group,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    period_title: str = "всё время",
) -> GroupSummary:
    contributions_stmt = _period_filter(
        select(Operation.author_id, func.sum(Operation.amount)).where(
            Operation.group_id == group.id,
            Operation.kind == CONTRIBUTION,
            Operation.deleted_at.is_(None),
        ),
        since,
        until,
    ).group_by(Operation.author_id)

    shares_stmt = _period_filter(
        select(OperationShare.user_id, func.sum(OperationShare.amount))
        .join(Operation, Operation.id == OperationShare.operation_id)
        .where(
            Operation.group_id == group.id,
            Operation.kind == PURCHASE,
            Operation.deleted_at.is_(None),
        ),
        since,
        until,
    ).group_by(OperationShare.user_id)

    contributed = {
        uid: int(total) for uid, total in await session.execute(contributions_stmt)
    }
    spent = {uid: int(total) for uid, total in await session.execute(shares_stmt)}

    members = await group_members(session, group.id)
    known = {user.id: user for user in members}
    # Бывший участник с историей операций всё равно должен попасть в баланс.
    for user_id in set(contributed) | set(spent):
        if user_id not in known:
            user = await session.get(User, user_id)
            if user:
                known[user_id] = user

    balances = [
        MemberBalance(
            user=user, contributed=contributed.get(uid, 0), spent=spent.get(uid, 0)
        )
        for uid, user in known.items()
    ]
    balances.sort(key=lambda item: (item.balance, item.user.display_name))

    return GroupSummary(
        group=group,
        period_title=period_title,
        total_contributed=sum(contributed.values()),
        total_spent=sum(spent.values()),
        members=balances,
    )


async def stats_by_category(
    session: AsyncSession,
    *,
    group_id: int,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> list[tuple[str, int]]:
    stmt = _period_filter(
        select(Operation.category, func.sum(Operation.amount)).where(
            Operation.group_id == group_id,
            Operation.kind == PURCHASE,
            Operation.deleted_at.is_(None),
        ),
        since,
        until,
    ).group_by(Operation.category)
    rows = [(code or "other", int(total)) for code, total in await session.execute(stmt)]
    rows.sort(key=lambda row: row[1], reverse=True)
    return rows


async def stats_by_person(
    session: AsyncSession,
    *,
    group_id: int,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> list[tuple[User, int]]:
    """Сколько расходов пришлось на долю каждого участника."""
    stmt = _period_filter(
        select(OperationShare.user_id, func.sum(OperationShare.amount))
        .join(Operation, Operation.id == OperationShare.operation_id)
        .where(
            Operation.group_id == group_id,
            Operation.kind == PURCHASE,
            Operation.deleted_at.is_(None),
        ),
        since,
        until,
    ).group_by(OperationShare.user_id)

    result: list[tuple[User, int]] = []
    for user_id, total in await session.execute(stmt):
        user = await session.get(User, user_id)
        if user:
            result.append((user, int(total)))
    result.sort(key=lambda row: row[1], reverse=True)
    return result


# --------------------------------------------------------------------------- #
#  Вход в веб-версию
# --------------------------------------------------------------------------- #


async def create_login_token(
    session: AsyncSession, user_id: int, ttl_minutes: int = 15
) -> str:
    token = secrets.token_urlsafe(24)
    session.add(
        WebLoginToken(
            token=token,
            user_id=user_id,
            expires_at=utcnow() + dt.timedelta(minutes=ttl_minutes),
        )
    )
    await session.flush()
    return token


async def consume_login_token(session: AsyncSession, token: str) -> User | None:
    row = await session.scalar(select(WebLoginToken).where(WebLoginToken.token == token))
    if row is None or row.used_at is not None or row.expires_at < utcnow():
        return None
    row.used_at = utcnow()
    await session.flush()
    return await session.get(User, row.user_id)
