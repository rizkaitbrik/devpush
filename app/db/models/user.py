from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, override

from sqlalchemy import Boolean, Enum as SQLAEnum, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base
from db.models._utils import get_fernet, utc_now

if TYPE_CHECKING:
    from db.models.team import Team


class User(Base):
    __tablename__: str = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(
        String(320), index=True, unique=True, nullable=False
    )
    username: Mapped[str] = mapped_column(
        String(50), index=True, unique=True, nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    has_avatar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(
        SQLAEnum("active", "deleted", name="team_status"),
        nullable=False,
        default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        index=True, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        index=True, nullable=False, default=utc_now, onupdate=utc_now
    )
    tokens_invalid_before: Mapped[datetime | None] = mapped_column(nullable=True)
    default_team_id: Mapped[str] = mapped_column(ForeignKey("team.id"), nullable=True)

    default_team: Mapped["Team"] = relationship(foreign_keys=[default_team_id])
    identities: Mapped[list["UserIdentity"]] = relationship(back_populates="user")

    @override
    def __repr__(self):
        return f"<User {self.email}>"


class UserIdentity(Base):
    __tablename__: str = "user_identity"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    provider: Mapped[str] = mapped_column(
        SQLAEnum("github", "google", name="identity_provider"),
        nullable=False,
        index=True,
    )
    provider_user_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    _access_token: Mapped[str | None] = mapped_column(
        "access_token", String(2048), nullable=True
    )
    _refresh_token: Mapped[str | None] = mapped_column(
        "refresh_token", String(2048), nullable=True
    )
    token_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)

    user: Mapped[User] = relationship(back_populates="identities")

    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_user_id", name="uq_identity_provider_user"
        ),
    )

    @property
    def access_token(self) -> str | None:
        if self._access_token:
            fernet = get_fernet()
            return fernet.decrypt(self._access_token.encode()).decode()
        return None

    @access_token.setter
    def access_token(self, value: str | None):
        if value:
            fernet = get_fernet()
            self._access_token = fernet.encrypt(value.encode()).decode()
        else:
            self._access_token = None

    @property
    def refresh_token(self) -> str | None:
        if self._refresh_token:
            fernet = get_fernet()
            return fernet.decrypt(self._refresh_token.encode()).decode()
        return None

    @refresh_token.setter
    def refresh_token(self, value: str | None):
        if value:
            fernet = get_fernet()
            self._refresh_token = fernet.encrypt(value.encode()).decode()
        else:
            self._refresh_token = None

    @override
    def __repr__(self):
        return f"<UserIdentity {self.provider}:{self.provider_user_id}>"
