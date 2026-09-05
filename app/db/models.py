from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow

CONTRIBUTION = "contribution"
PURCHASE = "purchase"
TRANSFER = "transfer"

# Режим расчётов в группе.
FUND = "fund"    # общая касса: скидываемся в фонд, покупки тратят его
SPLIT = "split"  # делим расходы: платит кто-то один, остальные ему должны
MODES = (FUND, SPLIT)


class Group(Base):
    """Независимый общий бюджет. Обычно привязан к чату, но может жить и без него."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="Общий бюджет")
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    mode: Mapped[str] = mapped_column(String(8), default=FUND, server_default=FUND)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_split(self) -> bool:
        return self.mode == SPLIT


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(128), default="")
    last_name: Mapped[str | None] = mapped_column(String(128))
    active_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def display_name(self) -> str:
        name = " ".join(p for p in (self.first_name, self.last_name) if p).strip()
        return name or (f"@{self.username}" if self.username else f"user{self.id}")

    @property
    def short_name(self) -> str:
        return (self.first_name or "").strip() or self.display_name


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    joined_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    group: Mapped[Group] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(lazy="selectin")


class Operation(Base):
    """Взнос в фонд, покупка или перевод долга между участниками.

    Перевод (transfer) живёт только в режиме дележа: автор — кто отдал деньги,
    единственная доля — кто их получил. Так возврат долга считается той же
    формулой, что и покупка, отдельной таблицы не нужно.

    Суммы хранятся в копейках, чтобы не терять точность на делении долей.
    """

    __tablename__ = "operations"
    __table_args__ = (Index("ix_op_group_time", "group_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(Integer)  # копейки, всегда > 0
    category: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(255))
    raw_text: Mapped[str | None] = mapped_column(String(512))
    category_source: Mapped[str | None] = mapped_column(String(16))  # llm|rules|manual
    source: Mapped[str] = mapped_column(String(16), default="dm")  # dm|group|inline|web
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, index=True)

    author: Mapped[User] = relationship(lazy="selectin")
    shares: Mapped[list["OperationShare"]] = relationship(
        back_populates="operation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def is_purchase(self) -> bool:
        return self.kind == PURCHASE

    @property
    def is_transfer(self) -> bool:
        return self.kind == TRANSFER

    @property
    def recipient(self) -> User | None:
        """Получатель перевода — единственная доля операции."""
        return self.shares[0].user if self.is_transfer and self.shares else None


class OperationShare(Base):
    """Доля конкретного участника в покупке (в копейках)."""

    __tablename__ = "operation_shares"
    __table_args__ = (UniqueConstraint("operation_id", "user_id", name="uq_share"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    amount: Mapped[int] = mapped_column(Integer)

    operation: Mapped[Operation] = relationship(back_populates="shares")
    user: Mapped[User] = relationship(lazy="selectin")


class DailyJob(Base):
    """Отметка, что ежедневное сообщение по группе уже отправлено.

    Хранится в базе, а не в памяти: иначе перезапуск в неудачный час прислал
    бы совет и напоминание повторно.
    """

    __tablename__ = "daily_jobs"
    __table_args__ = (UniqueConstraint("group_id", "job", name="uq_daily_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    job: Mapped[str] = mapped_column(String(16))  # tips | debts
    sent_on: Mapped[dt.date] = mapped_column(Date)


class WebLoginToken(Base):
    """Одноразовый токен для входа в веб-версию из бота."""

    __tablename__ = "web_login_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
