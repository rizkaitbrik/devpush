from __future__ import annotations

from datetime import datetime
from typing import override

from sqlalchemy import Enum as SQLAEnum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base
from db.models._utils import utc_now
from db.models.project import Project


class Domain(Base):
    __tablename__: str = "domain"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), index=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    type: Mapped[str] = mapped_column(
        SQLAEnum("route", "301", "302", "307", "308", name="domain_type"),
        nullable=False,
    )
    environment_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(
        SQLAEnum("pending", "active", "disabled", "failed", name="domain_status"),
        nullable=False,
        default="pending",
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)

    project: Mapped[Project] = relationship(back_populates="domains")

    @override
    def __repr__(self):
        return f"<Domain {self.hostname}>"
