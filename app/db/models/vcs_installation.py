from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from secrets import token_hex
from typing import TYPE_CHECKING, override

from cryptography.fernet import Fernet
from sqlalchemy import Enum as SQLAEnum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config import get_settings
from db import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@lru_cache
def _get_fernet() -> Fernet:
    return Fernet(get_settings().encryption_key)


if TYPE_CHECKING:
    from db.models.project import Project


class VcsInstallation(Base):
    """Stores app/OAuth installation records for VCS provider integrations."""

    __tablename__: str = "vcs_installation"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: token_hex(16)
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="github")
    provider_account_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    installation_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    _token: Mapped[str | None] = mapped_column("token", String(2048), nullable=True)
    _refresh_token: Mapped[str | None] = mapped_column("refresh_token", String(2048), nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(
        SQLAEnum("active", "deleted", "suspended", name="vcs_installation_status"),
        nullable=False,
        default="active",
    )

    projects: Mapped[list[Project]] = relationship(back_populates="vcs_installation")

    @property
    def token(self) -> str | None:
        if self._token is None:
            return None
        return _get_fernet().decrypt(self._token.encode()).decode()

    @token.setter
    def token(self, value: str):
        if not value:
            self._token = None
        else:
            self._token = _get_fernet().encrypt(value.encode()).decode()

    @property
    def refresh_token(self) -> str | None:
        if self._refresh_token is None:
            return None
        return _get_fernet().decrypt(self._refresh_token.encode()).decode()

    @refresh_token.setter
    def refresh_token(self, value: str | None):
        if not value:
            self._refresh_token = None
        else:
            self._refresh_token = _get_fernet().encrypt(value.encode()).decode()

    @override
    def __repr__(self):
        return f"<VcsInstallation {self.provider}:{self.provider_account_id}>"
