from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from secrets import token_hex
from typing import TYPE_CHECKING, override

from cryptography.fernet import Fernet
from sqlalchemy import ForeignKey, String, Text, Index, UniqueConstraint
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


class EnvVar(Base):
    """A single environment variable for a project, stored encrypted.

    environment=None means the variable applies to all environments.
    A row with a specific environment slug overrides the global row for that environment.
    """

    __tablename__ = "env_var"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: token_hex(16)
    )
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("project.id", ondelete="CASCADE"), index=True, nullable=False
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    _value: Mapped[str] = mapped_column("value", Text, nullable=False, default="")
    environment: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, default=_utc_now, onupdate=_utc_now
    )

    project: Mapped[Project] = relationship(back_populates="env_var_rows")

    __table_args__ = (
        UniqueConstraint("project_id", "key", "environment", name="uq_env_var_project_key_env"),
        Index("ix_env_var_project_id", "project_id"),
    )

    @property
    def value(self) -> str:
        if not self._value:
            return ""
        return _get_fernet().decrypt(self._value.encode()).decode()

    @value.setter
    def value(self, raw: str):
        self._value = _get_fernet().encrypt((raw or "").encode()).decode()

    @override
    def __repr__(self):
        env = f"[{self.environment}]" if self.environment else "[*]"
        return f"<EnvVar {self.key}{env}>"
