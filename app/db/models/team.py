from __future__ import annotations

import re
from datetime import datetime, timedelta
from secrets import token_hex
from typing import TYPE_CHECKING, override

from sqlalchemy import Boolean, Enum as SQLAEnum, ForeignKey, String, event, func, select, update
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base
from db.models._utils import FORBIDDEN_TEAM_SLUGS, utc_now
from db.models.user import User
from utils.color import get_color

if TYPE_CHECKING:
    from db.models.project import Project
    from db.models.storage import Storage


class Team(Base):
    __tablename__: str = "team"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: token_hex(16)
    )
    name: Mapped[str] = mapped_column(String(100), index=True)
    slug: Mapped[str] = mapped_column(String(40), nullable=True, unique=True)
    has_avatar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(
        SQLAEnum("active", "deleted", name="team_status"),
        nullable=False,
        default="active",
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", use_alter=True, ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        index=True, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        index=True, nullable=False, default=utc_now, onupdate=utc_now
    )

    projects: Mapped[list["Project"]] = relationship(back_populates="team")
    storages: Mapped[list["Storage"]] = relationship(back_populates="team")
    created_by_user: Mapped[User | None] = relationship(
        foreign_keys=[created_by_user_id]
    )

    @property
    def color(self) -> str:
        return get_color(self.id)


@event.listens_for(Team, "after_insert")
def set_team_slug(mapper, connection, team):
    """Generate and set slug after team is inserted (and has an ID)."""
    if not team.slug:
        base_slug = team.name.lower()
        base_slug = base_slug.replace(" ", "-").replace("_", "-").replace(".", "-")
        base_slug = re.sub(r"[^a-z0-9-]", "", base_slug)
        base_slug = re.sub(r"-+", "-", base_slug)
        base_slug = base_slug[:40].strip("-")
        if not base_slug or base_slug in FORBIDDEN_TEAM_SLUGS:
            base_slug = f"team-{team.id}"[:40]

        new_slug = (
            base_slug
            if not connection.scalar(
                select(Team.slug).where(func.lower(Team.slug) == base_slug.lower())
            )
            else f"{base_slug[:32]}-{str(team.id)[:7]}"
        )

        connection.execute(update(Team).where(Team.id == team.id).values(slug=new_slug))
        team.slug = new_slug


class TeamMember(Base):
    __tablename__ = "team_member"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("team.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    role: Mapped[str] = mapped_column(
        SQLAEnum("owner", "admin", "member", name="team_member_role"),
        nullable=False,
        default="member",
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)

    team: Mapped[Team] = relationship()
    user: Mapped[User] = relationship()


class TeamInvite(Base):
    __tablename__ = "team_invite"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: token_hex(16)
    )
    team_id: Mapped[str] = mapped_column(ForeignKey("team.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(
        SQLAEnum("owner", "admin", "member", name="team_invite_role"),
        nullable=False,
        default="member",
    )
    status: Mapped[str] = mapped_column(
        SQLAEnum("pending", "accepted", "revoked", name="team_invite_status"),
        nullable=False,
        default="pending",
    )
    inviter_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(
        default=lambda: utc_now() + timedelta(days=30)
    )

    team: Mapped[Team] = relationship()
    inviter: Mapped[User] = relationship()
