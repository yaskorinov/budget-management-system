"""Вся бизнес-логика общего котла. Хендлеры бота и API — тонкие обёртки над ней."""
from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass, field

from sqlalchemy import Select, func, inspect as sa_inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.money import split_amount
from app.db.base import utcnow
from app.db.models import (
    CONTRIBUTION,
    LINK,
    LOGIN,
    FUND,
    MODES,
    PURCHASE,
    SPLIT,
    TRANSFER,
    DailyJob,
    Group,
    GroupInvite,
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
    session: AsyncSession, *, tg_chat_id: int, title: str, mode: str = FUND
) -> tuple[Group, bool]:
    """Бюджет чата и признак того, что он только что создан.

    Признак нужен вызывающему: у нового бюджета стоит спросить режим расчётов,
    у существующего — молчать.
    """
    group = await session.scalar(select(Group).where(Group.tg_chat_id == tg_chat_id))
    if group is not None:
        if title and group.title != title[:255]:
            group.title = title[:255]
        return group, False

    group = Group(
        tg_chat_id=tg_chat_id,
        title=title[:255] or "Общий бюджет",
        mode=mode if mode in MODES else FUND,
    )
    session.add(group)
    await session.flush()
    return group, True


async def create_group(
    session: AsyncSession, *, title: str, owner: User, mode: str = FUND
) -> Group:
    group = Group(title=(title or "Общий бюджет")[:255], mode=mode if mode in MODES else FUND)
    session.add(group)
    await session.flush()
    await ensure_member(session, group_id=group.id, user_id=owner.id, is_admin=True)
    if owner.active_group_id is None:
        owner.active_group_id = group.id
    await session.flush()
    return group


async def set_group_mode(session: AsyncSession, *, group: Group, mode: str) -> Group:
    """Меняет режим расчётов.

    Операции чужого режима не переносим: взносы в кассу и переводы долга
    считаются по-разному, и молча пересчитать историю нельзя.
    """
    if mode not in MODES:
        raise ServiceError("Неизвестный режим бюджета")
    if mode == group.mode:
        return group

    stale = CONTRIBUTION if mode == SPLIT else TRANSFER
    left = await session.scalar(
        select(func.count())
        .select_from(Operation)
        .where(
            Operation.group_id == group.id,
            Operation.kind == stale,
            Operation.deleted_at.is_(None),
        )
    )
    if left:
        raise ServiceError(
            "В бюджете есть операции, которых в новом режиме не бывает. "
            "Удалите их или заведите отдельный бюджет"
        )

    group.mode = mode
    await session.flush()
    return group


async def can_set_mode(session: AsyncSession, *, group: Group, user_id: int) -> bool:
    """Режим меняет админ бюджета. Пока не записано ни одной операции — любой
    участник: сразу после создания админа может ещё не быть."""
    membership = await session.scalar(
        select(Membership).where(
            Membership.group_id == group.id,
            Membership.user_id == user_id,
            Membership.is_active.is_(True),
        )
    )
    if membership is None:
        return False
    if membership.is_admin:
        return True
    used = await session.scalar(
        select(func.count())
        .select_from(Operation)
        .where(Operation.group_id == group.id, Operation.deleted_at.is_(None))
    )
    return not used


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


async def grant_admins(session: AsyncSession, *, group_id: int, tg_user_ids: list[int]) -> int:
    """Отмечает админами тех, кто админ в самом чате.

    Права только выдаём: снимать их по составу чата нельзя, иначе слетит
    админ, назначенный в самом боте.
    """
    if not tg_user_ids:
        return 0

    rows = await session.execute(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(Membership.group_id == group_id, User.tg_user_id.in_(tg_user_ids))
    )
    granted = 0
    for membership in rows.scalars():
        if not membership.is_admin:
            membership.is_admin = True
            granted += 1
    if granted:
        await session.flush()
    return granted


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

    group = await session.get(Group, group_id)
    if group is not None and group.is_split:
        raise ServiceError("Здесь нет общей кассы: долг возвращают переводом")

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


async def add_transfer(
    session: AsyncSession,
    *,
    group_id: int,
    author_id: int,
    to_user_id: int,
    amount: int,
    source: str = "dm",
    occurred_at: dt.datetime | None = None,
    comment: str | None = None,
) -> Operation:
    """Возврат долга: автор отдал деньги, получатель их принял.

    Хранится как операция с единственной долей на получателя — тогда балансы
    считаются той же формулой, что и по покупкам.
    """
    if amount <= 0:
        raise ServiceError("Сумма должна быть больше нуля")
    if to_user_id == author_id:
        raise ServiceError("Перевод самому себе ничего не меняет")

    group = await session.get(Group, group_id)
    if group is not None and not group.is_split:
        raise ServiceError("Здесь общая касса: деньги вносят в фонд, а не переводом")

    author = await session.get(User, author_id)
    if author is None:
        raise ServiceError("Автор операции не найден")
    if not await is_member(session, group_id=group_id, user_id=to_user_id):
        raise ServiceError("Получатель не участвует в этом бюджете")

    operation = Operation(
        group_id=group_id,
        author=author,
        kind=TRANSFER,
        amount=amount,
        title=(comment or None),
        source=source,
        occurred_at=occurred_at or utcnow(),
    )
    session.add(operation)
    await session.flush()
    await _set_shares(session, operation, [to_user_id])
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

    if (operation.is_purchase or operation.is_transfer) and (
        participant_ids is not None or amount is not None
    ):
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
    """Сколько человек внёс и сколько с него причитается.

    В общей кассе «внёс» — это пополнения фонда, а «доля» — часть покупок.
    В режиме дележа «внёс» — всё, что человек оплатил из своего кармана
    (покупки и возвраты долга), а «доля» — его часть покупок плюс полученные
    возвраты. Знак баланса в обоих режимах означает одно и то же.
    """

    user: User
    contributed: int = 0
    spent: int = 0

    @property
    def balance(self) -> int:
        """Больше нуля — переплатил; меньше — за ним долг."""
        return self.contributed - self.spent


@dataclass(slots=True)
class Debt:
    """Один перевод из плана взаимозачёта: должник → кредитор."""

    debtor: User
    creditor: User
    amount: int


def simplify_debts(balances: list[MemberBalance]) -> list[Debt]:
    """Кто кому сколько должен — минимальным числом переводов.

    Жадный зачёт: самый крупный должник гасит долг самому крупному кредитору,
    потом остаток переходит дальше. Переводов выходит не больше, чем
    участников минус один, — платить каждому за каждую покупку не нужно.
    """
    debtors = sorted(
        ([item.user, -item.balance] for item in balances if item.balance < 0),
        key=lambda item: (-item[1], item[0].display_name),
    )
    creditors = sorted(
        ([item.user, item.balance] for item in balances if item.balance > 0),
        key=lambda item: (-item[1], item[0].display_name),
    )

    debts: list[Debt] = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        amount = min(debtors[i][1], creditors[j][1])
        if amount > 0:
            debts.append(
                Debt(debtor=debtors[i][0], creditor=creditors[j][0], amount=amount)
            )
        debtors[i][1] -= amount
        creditors[j][1] -= amount
        if debtors[i][1] == 0:
            i += 1
        if creditors[j][1] == 0:
            j += 1
    return debts


@dataclass(slots=True)
class GroupSummary:
    group: Group
    period_title: str
    total_contributed: int = 0
    total_spent: int = 0
    members: list[MemberBalance] = field(default_factory=list)
    debts: list[Debt] = field(default_factory=list)

    @property
    def mode(self) -> str:
        return self.group.mode

    @property
    def is_split(self) -> bool:
        return self.group.is_split

    @property
    def fund_left(self) -> int:
        """Остаток кассы. В режиме дележа кассы нет — там всегда ноль."""
        return 0 if self.is_split else self.total_contributed - self.total_spent

    def balance_of(self, user_id: int) -> int:
        for item in self.members:
            if item.user.id == user_id:
                return item.balance
        return 0


async def summary(
    session: AsyncSession,
    *,
    group: Group,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    period_title: str = "всё время",
) -> GroupSummary:
    split = group.is_split
    # Кто сколько выложил и кому сколько причитается — набор видов операций
    # разный, а формула одна.
    paid_kinds = (PURCHASE, TRANSFER) if split else (CONTRIBUTION,)
    share_kinds = (PURCHASE, TRANSFER) if split else (PURCHASE,)

    paid_stmt = _period_filter(
        select(Operation.author_id, func.sum(Operation.amount)).where(
            Operation.group_id == group.id,
            Operation.kind.in_(paid_kinds),
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
            Operation.kind.in_(share_kinds),
            Operation.deleted_at.is_(None),
        ),
        since,
        until,
    ).group_by(OperationShare.user_id)

    spent_stmt = _period_filter(
        select(func.sum(Operation.amount)).where(
            Operation.group_id == group.id,
            Operation.kind == PURCHASE,
            Operation.deleted_at.is_(None),
        ),
        since,
        until,
    )

    contributed = {uid: int(total) for uid, total in await session.execute(paid_stmt)}
    spent = {uid: int(total) for uid, total in await session.execute(shares_stmt)}
    total_spent = int(await session.scalar(spent_stmt) or 0)

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
        total_contributed=0 if split else sum(contributed.values()),
        total_spent=total_spent,
        members=balances,
        debts=simplify_debts(balances) if split else [],
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


async def claim_daily_job(
    session: AsyncSession, *, group_id: int, job: str, today: dt.date
) -> bool:
    """Занимает ежедневное задание. False — сегодня его уже выполняли."""
    row = await session.scalar(
        select(DailyJob).where(DailyJob.group_id == group_id, DailyJob.job == job)
    )
    if row is None:
        session.add(DailyJob(group_id=group_id, job=job, sent_on=today))
        await session.flush()
        return True
    if row.sent_on >= today:
        return False
    row.sent_on = today
    await session.flush()
    return True


async def groups_with_chats(session: AsyncSession) -> list[Group]:
    """Бюджеты, привязанные к чату: только в них есть куда написать."""
    rows = await session.scalars(select(Group).where(Group.tg_chat_id.is_not(None)))
    return list(rows)


async def create_login_token(
    session: AsyncSession,
    user_id: int,
    ttl_minutes: int = 15,
    purpose: str = LOGIN,
) -> str:
    token = secrets.token_urlsafe(24)
    session.add(
        WebLoginToken(
            token=token,
            user_id=user_id,
            purpose=purpose,
            expires_at=utcnow() + dt.timedelta(minutes=ttl_minutes),
        )
    )
    await session.flush()
    return token


async def consume_login_token(
    session: AsyncSession, token: str, purpose: str = LOGIN
) -> User | None:
    row = await session.scalar(
        select(WebLoginToken).where(
            WebLoginToken.token == token, WebLoginToken.purpose == purpose
        )
    )
    if row is None or row.used_at is not None or row.expires_at < utcnow():
        return None
    row.used_at = utcnow()
    await session.flush()
    return await session.get(User, row.user_id)


# --------------------------------------------------------------------------- #
#  Приглашения в бюджет
# --------------------------------------------------------------------------- #


async def create_invite(
    session: AsyncSession,
    *,
    group_id: int,
    created_by: int,
    ttl_days: int = 7,
    max_uses: int = 25,
) -> GroupInvite:
    """Новая ссылка гасит прежние ссылки этой группы — это и есть отзыв."""
    await session.execute(
        update(GroupInvite)
        .where(GroupInvite.group_id == group_id, GroupInvite.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    invite = GroupInvite(
        token=secrets.token_urlsafe(18),
        group_id=group_id,
        created_by=created_by,
        expires_at=utcnow() + dt.timedelta(days=ttl_days),
        max_uses=max_uses,
    )
    session.add(invite)
    await session.flush()
    return invite


async def get_invite(session: AsyncSession, token: str) -> GroupInvite | None:
    """Живое приглашение или None: протухшее, отозванное и исчерпанное — не в счёт."""
    invite = await session.scalar(
        select(GroupInvite).where(GroupInvite.token == token)
    )
    if invite is None or invite.revoked_at is not None:
        return None
    if invite.expires_at < utcnow() or invite.uses >= invite.max_uses:
        return None
    return invite


async def accept_invite(
    session: AsyncSession, *, invite: GroupInvite, user: User
) -> Group:
    already = await is_member(session, group_id=invite.group_id, user_id=user.id)
    await ensure_member(session, group_id=invite.group_id, user_id=user.id)
    if not already:
        invite.uses += 1
    user.active_group_id = invite.group_id
    await session.flush()
    return await session.get(Group, invite.group_id)


async def create_guest(session: AsyncSession, *, name: str) -> User:
    """Аккаунт без Telegram и Яндекса: живёт токеном в браузере."""
    user = User(first_name=(name or "").strip()[:128] or "Гость")
    session.add(user)
    await session.flush()
    return user


# --------------------------------------------------------------------------- #
#  Яндекс ID и привязка Telegram
# --------------------------------------------------------------------------- #


async def user_by_yandex(session: AsyncSession, yandex_id: str) -> User | None:
    return await session.scalar(select(User).where(User.yandex_id == yandex_id))


async def attach_yandex(
    session: AsyncSession,
    *,
    user: User,
    yandex_id: str,
    email: str | None = None,
    name: str | None = None,
) -> User:
    taken = await user_by_yandex(session, yandex_id)
    if taken is not None and taken.id != user.id:
        raise ServiceError("Этот Яндекс ID уже привязан к другому аккаунту")

    user.yandex_id = yandex_id
    if email:
        user.email = email[:255]
    if name and not (user.first_name or "").strip():
        user.first_name = name[:128]
    await session.flush()
    return user


async def has_history(session: AsyncSession, user_id: int) -> bool:
    """Есть ли за аккаунтом что-то, что нельзя потерять при привязке."""
    operations = await session.scalar(
        select(func.count())
        .select_from(Operation)
        .where(Operation.author_id == user_id, Operation.deleted_at.is_(None))
    )
    if operations:
        return True
    shares = await session.scalar(
        select(func.count()).select_from(OperationShare).where(
            OperationShare.user_id == user_id
        )
    )
    if shares:
        return True
    memberships = await session.scalar(
        select(func.count()).select_from(Membership).where(
            Membership.user_id == user_id, Membership.is_active.is_(True)
        )
    )
    return bool(memberships)


async def link_telegram(
    session: AsyncSession, *, web_user: User, tg_user: User
) -> User:
    """Привязывает Telegram к веб-аккаунту.

    Слияние двух живых аккаунтов пока не поддерживаем: перенос операций, долей
    и участий — отдельная задача, а тихо потерять их нельзя. Поэтому привязка
    разрешена, только если со стороны Telegram истории ещё нет.
    """
    if web_user.id == tg_user.id:
        raise ServiceError("Этот аккаунт уже привязан к Telegram")
    if web_user.tg_user_id is not None:
        raise ServiceError("К аккаунту уже привязан другой Telegram")
    if await has_history(session, tg_user.id):
        raise ServiceError(
            "В этом Telegram уже есть свои бюджеты и операции. "
            "Объединение аккаунтов пока не поддерживается"
        )

    # Сначала освобождаем идентификатор: он уникален, и обе записи одновременно
    # держать его не могут — порядок в одном flush не гарантирован.
    tg_id, username = tg_user.tg_user_id, tg_user.username
    first_name, last_name = tg_user.first_name, tg_user.last_name
    tg_user.tg_user_id = None
    await session.flush()
    await session.delete(tg_user)
    await session.flush()

    web_user.tg_user_id = tg_id
    web_user.username = username or web_user.username
    if not (web_user.first_name or "").strip():
        web_user.first_name = first_name
        web_user.last_name = last_name
    await session.flush()
    return web_user
