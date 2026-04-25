from __future__ import annotations

from datetime import datetime
from secrets import token_hex
from typing import TYPE_CHECKING, override

from sqlalchemy import Enum as SQLAEnum, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base
from db.models._utils import utc_now
from db.models.user import User

if TYPE_CHECKING:
    from db.models.project import Project
    from db.models.team import Team


class Storage(Base):
    __tablename__: str = "storage"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: token_hex(16)
    )
    name: Mapped[str] = mapped_column(String(100), index=True)
    type: Mapped[str] = mapped_column(
        SQLAEnum("database", "volume", "kv", "queue", name="storage_type"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        SQLAEnum("pending", "active", "resetting", "deleted", name="storage_status"),
        nullable=False,
        default="pending",
    )
    config: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    error: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", use_alter=True, ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        index=True, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        index=True, nullable=False, default=utc_now, onupdate=utc_now
    )
    team_id: Mapped[str] = mapped_column(ForeignKey("team.id"), index=True)

    team: Mapped["Team"] = relationship(back_populates="storages")
    created_by_user: Mapped[User | None] = relationship(
        foreign_keys=[created_by_user_id]
    )
    project_links: Mapped[list["StorageProject"]] = relationship(
        back_populates="storage"
    )

    __table_args__ = (
        UniqueConstraint("team_id", "name", name="uq_storage_team_name"),
        Index(
            "ix_storage_team_name_lower",
            "team_id",
            func.lower(name),
            unique=True,
        ),
    )

    @override
    def __repr__(self):
        return f"<Storage {self.name} ({self.type})>"

    @property
    def projects(self) -> list["Project"]:
        return [link.project for link in self.project_links if link.project]

    @property
    def color(self) -> str:
        match self.type:
            case "database":
                return "sky"
            case "volume":
                return "amber"
            case "kv":
                return "rose"
            case "queue":
                return "green"


class StorageProject(Base):
    __tablename__: str = "storage_project"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: token_hex(16)
    )
    storage_id: Mapped[str] = mapped_column(ForeignKey("storage.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), index=True)
    environment_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    secrets: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, default=utc_now, onupdate=utc_now
    )

    project: Mapped["Project"] = relationship(back_populates="storage_links")
    storage: Mapped["Storage"] = relationship(back_populates="project_links")

    __table_args__ = (
        UniqueConstraint(
            "storage_id",
            "project_id",
            name="uq_storage_project",
        ),
    )
