"""REST API для мини-аппы и браузерной версии."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.auth import current_user, issue_session, verify_init_data
from app.config import settings
from app.core import categories as cat
from app.core import periods, reports, service
from app.core.classifier import parse_purchase
from app.db.base import get_session
from app.db.models import Group, Operation, User

router = APIRouter()


def _user_out(user: User) -> schemas.UserOut:
    return schemas.UserOut(id=user.id, name=user.display_name, username=user.username)


def _group_out(group: Group) -> schemas.GroupOut:
    return schemas.GroupOut(
        id=group.id, title=group.title, currency=group.currency, mode=group.mode
    )


def _operation_out(operation: Operation, viewer: User, can_edit: bool) -> schemas.OperationOut:
    category = cat.get(operation.category) if operation.is_purchase else None
    recipient = operation.recipient
    return schemas.OperationOut(
        id=operation.id,
        kind=operation.kind,
        amount=operation.amount,
        category=operation.category if operation.is_purchase else None,
        category_title=category.title if category else None,
        title=operation.title,
        author_id=operation.author_id,
        author=operation.author.display_name,
        occurred_at=operation.occurred_at,
        can_edit=can_edit,
        shares=[
            schemas.ShareOut(
                user_id=share.user_id, name=share.user.short_name, amount=share.amount
            )
            for share in operation.shares
        ],
        to_user_id=recipient.id if recipient else None,
        to_user=recipient.short_name if recipient else None,
    )


async def _auth_payload(session: AsyncSession, user: User) -> schemas.AuthOut:
    groups = await service.user_groups(session, user.id)
    active = await service.resolve_active_group(session, user)
    return schemas.AuthOut(
        token=issue_session(user.id),
        user=_user_out(user),
        groups=[_group_out(group) for group in groups],
        active_group_id=active.id if active else None,
    )


async def _group_for(session: AsyncSession, user: User, group_id: int) -> Group:
    group = await session.get(Group, group_id)
    if group is None or not await service.is_member(
        session, group_id=group_id, user_id=user.id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Группа не найдена")
    return group


# --------------------------------------------------------------------------- #
#  Аутентификация
# --------------------------------------------------------------------------- #


@router.post("/auth/telegram", response_model=schemas.AuthOut)
async def auth_telegram(
    payload: schemas.TelegramAuthIn, session: AsyncSession = Depends(get_session)
):
    tg_user = verify_init_data(payload.init_data)
    if not tg_user or not tg_user.get("id"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Подпись Telegram не сошлась")

    user = await service.get_or_create_user(
        session,
        tg_user_id=int(tg_user["id"]),
        first_name=tg_user.get("first_name", ""),
        last_name=tg_user.get("last_name"),
        username=tg_user.get("username"),
    )
    return await _auth_payload(session, user)


@router.post("/auth/magic", response_model=schemas.AuthOut)
async def auth_magic(
    payload: schemas.MagicAuthIn, session: AsyncSession = Depends(get_session)
):
    user = await service.consume_login_token(session, payload.token)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ссылка недействительна")
    return await _auth_payload(session, user)


@router.get("/me", response_model=schemas.AuthOut)
async def me(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    return await _auth_payload(session, user)


@router.post("/me/active-group/{group_id}", response_model=schemas.AuthOut)
async def set_active(
    group_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        await service.set_active_group(session, user, group_id)
    except service.ServiceError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return await _auth_payload(session, user)


@router.get("/categories", response_model=list[schemas.CategoryOut])
async def categories_list():
    return [
        schemas.CategoryOut(
            code=item.code, title=item.title, emoji=item.emoji, color=item.color
        )
        for item in cat.CATEGORIES
    ]


# --------------------------------------------------------------------------- #
#  Группа: сводка, участники, операции
# --------------------------------------------------------------------------- #


@router.get("/groups/{group_id}/summary", response_model=schemas.SummaryOut)
async def group_summary(
    group_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    group = await _group_for(session, user, group_id)
    data = await service.summary(session, group=group)
    return schemas.SummaryOut(
        group=_group_out(group),
        mode=group.mode,
        fund_left=data.fund_left,
        total_contributed=data.total_contributed,
        total_spent=data.total_spent,
        members=[
            schemas.BalanceOut(
                user_id=item.user.id,
                name=item.user.short_name,
                contributed=item.contributed,
                spent=item.spent,
                balance=item.balance,
            )
            for item in data.members
        ],
        debts=[
            schemas.DebtOut(
                from_user_id=debt.debtor.id,
                from_name=debt.debtor.short_name,
                to_user_id=debt.creditor.id,
                to_name=debt.creditor.short_name,
                amount=debt.amount,
            )
            for debt in data.debts
        ],
    )


@router.get("/groups/{group_id}/members", response_model=list[schemas.UserOut])
async def group_members(
    group_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await _group_for(session, user, group_id)
    members = await service.group_members(session, group_id)
    return [_user_out(member) for member in members]


@router.get("/groups/{group_id}/operations", response_model=list[schemas.OperationOut])
async def group_operations(
    group_id: int,
    scope: str = Query("all", pattern="^(all|mine)$"),
    period: str = Query("all"),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await _group_for(session, user, group_id)
    since, until, _ = periods.bounds(period)
    operations = await service.list_operations(
        session,
        group_id=group_id,
        author_id=user.id if scope == "mine" else None,
        limit=limit,
        offset=offset,
        since=since,
        until=until,
    )
    result = []
    for operation in operations:
        can_edit = await service.can_manage(session, operation, user)
        result.append(_operation_out(operation, user, can_edit))
    return result


@router.post(
    "/groups/{group_id}/operations",
    response_model=schemas.OperationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_operation(
    group_id: int,
    payload: schemas.OperationIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await _group_for(session, user, group_id)
    try:
        if payload.kind == "transfer":
            if not payload.to_user_id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "Не указан получатель перевода"
                )
            operation = await service.add_transfer(
                session,
                group_id=group_id,
                author_id=user.id,
                to_user_id=payload.to_user_id,
                amount=payload.amount,
                source="web",
                occurred_at=payload.occurred_at,
                comment=payload.title,
            )
        elif payload.kind == "contribution":
            operation = await service.add_contribution(
                session,
                group_id=group_id,
                author_id=user.id,
                amount=payload.amount,
                source="web",
                occurred_at=payload.occurred_at,
                comment=payload.title,
            )
        else:
            operation = await service.add_purchase(
                session,
                group_id=group_id,
                author_id=user.id,
                amount=payload.amount,
                category=payload.category or cat.OTHER,
                title=payload.title or cat.get(payload.category).title,
                participant_ids=payload.participant_ids,
                source="web",
                category_source="manual" if payload.category else "rules",
                occurred_at=payload.occurred_at,
            )
    except service.ServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _operation_out(operation, user, True)


@router.patch("/operations/{operation_id}", response_model=schemas.OperationOut)
async def patch_operation(
    operation_id: int,
    payload: schemas.OperationPatch,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    operation = await service.get_operation(session, operation_id)
    if operation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Операция не найдена")
    if not await service.can_manage(session, operation, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это не ваша операция")

    try:
        await service.edit_operation(
            session,
            operation,
            amount=payload.amount,
            category=payload.category,
            title=payload.title,
            participant_ids=payload.participant_ids,
            occurred_at=payload.occurred_at,
        )
    except service.ServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _operation_out(operation, user, True)


@router.delete("/operations/{operation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_operation(
    operation_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    operation = await service.get_operation(session, operation_id)
    if operation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Операция не найдена")
    if not await service.can_manage(session, operation, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это не ваша операция")
    await service.delete_operation(session, operation)


# --------------------------------------------------------------------------- #
#  Статистика и разбор текста
# --------------------------------------------------------------------------- #


@router.get("/groups/{group_id}/stats", response_model=schemas.StatsOut)
async def group_stats(
    group_id: int,
    mode: str = Query("categories"),
    period: str = Query("month"),
    png: bool = Query(False, description="дополнительно отрисовать PNG и вернуть ссылку"),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    group = await _group_for(session, user, group_id)
    report = await reports.build(session, group=group, mode=mode, period=period)

    chart_url = None
    if png and not report.is_empty:
        image = reports.render_png(report)
        if image:
            chart_url = f"{settings.public_base}/charts/{reports.save_png(image)}"

    return schemas.StatsOut(
        mode=report.mode,
        period=report.period,
        period_title=report.period_title,
        total=report.total,
        slices=[
            schemas.SliceOut(label=item.label, value=item.value, color=item.color)
            for item in report.slices
        ],
        chart_url=chart_url,
    )


@router.post("/categorize", response_model=schemas.CategorizeOut)
async def categorize(
    payload: schemas.CategorizeIn, user: User = Depends(current_user)
):
    parsed = await parse_purchase(payload.text)
    return schemas.CategorizeOut(
        amount=parsed.amount,
        category=parsed.category,
        category_title=cat.get(parsed.category).title,
        title=parsed.title,
        source=parsed.source,
    )


charts_router = APIRouter()


@charts_router.get("/charts/{name}")
async def chart_file(name: str):
    """Отдаёт кэшированную диаграмму — по этой ссылке её забирает Telegram."""
    path = reports.chart_path(name)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Диаграмма не найдена")
    return FileResponse(path, media_type="image/png")
