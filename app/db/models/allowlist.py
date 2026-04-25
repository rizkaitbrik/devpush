from __future__ import annotations

from datetime import datetime
from typing import override

from sqlalchemy import Enum as SQLAEnum, String
from sqlalchemy.orm import Mapped, mapped_column

from db import Base
from db.models._utils import utc_now


class Allowlist(Base):
    __tablename__: str = "allowlist"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(
        SQLAEnum("email", "domain", "pattern", name="allowlist_type"),
        nullable=False,
        index=True,
    )
    value: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        index=True, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        index=True, nullable=False, default=utc_now, onupdate=utc_now
    )

    @override
    def __repr__(self):
        return f"<Allowlist {self.type}:{self.value}>"
