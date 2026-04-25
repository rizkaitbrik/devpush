from __future__ import annotations

import json
from datetime import datetime
from secrets import token_hex
from typing import TYPE_CHECKING, override

from sqlalchemy import (
    BigInteger,
    Enum as SQLAEnum,
    ForeignKey,
    JSON,
    String,
    Text,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config import get_settings
from db import Base
from db.models._utils import get_fernet, utc_now
from db.models.project import Project
from db.models.user import User
from utils.log import parse_log

if TYPE_CHECKING:
    pass


class Deployment(Base):
    __tablename__: str = "deployment"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: token_hex(16)
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), index=True)
    repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    environment_id: Mapped[str] = mapped_column(String(8), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), index=True)
    commit_sha: Mapped[str] = mapped_column(String(40), index=True)
    commit_meta: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    config: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    _env_vars: Mapped[str] = mapped_column("env_vars", Text, nullable=False, default="")
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    container_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    container_status: Mapped[str | None] = mapped_column(
        SQLAEnum("running", "stopped", "removed", name="deployment_container_status"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        SQLAEnum(
            "prepare",
            "deploy",
            "finalize",
            "fail",
            "completed",
            name="deployment_status",
        ),
        nullable=False,
        default="prepare",
    )
    conclusion: Mapped[str] = mapped_column(
        SQLAEnum(
            "succeeded", "failed", "canceled", "skipped", name="deployment_conclusion"
        ),
        nullable=True,
    )
    trigger: Mapped[str] = mapped_column(
        SQLAEnum("webhook", "user", "api", name="deployment_trigger"),
        nullable=False,
        default="user",
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", use_alter=True, ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        index=True, nullable=False, default=utc_now
    )
    concluded_at: Mapped[datetime | None] = mapped_column(index=True, nullable=True)

    project: Mapped[Project] = relationship(back_populates="deployments")
    aliases: Mapped[list["Alias"]] = relationship(
        back_populates="deployment", foreign_keys="Alias.deployment_id"
    )
    created_by_user: Mapped[User | None] = relationship(
        foreign_keys=[created_by_user_id]
    )

    def __init__(self, *args, project: "Project", environment_id: str, **kwargs):
        super().__init__(project=project, environment_id=environment_id, **kwargs)
        self.repo_id = project.repo_id
        self.repo_full_name = project.repo_full_name
        self.config = project.config
        environment = project.get_environment_by_id(environment_id)
        self.env_vars = project.get_env_vars(environment["slug"]) if environment else []

    @property
    def environment(self) -> dict | None:
        """Get environment configuration"""
        return self.project.get_environment_by_id(self.environment_id)

    @property
    def env_vars(self) -> list[dict[str, str]]:
        if self._env_vars:
            fernet = get_fernet()
            decrypted = fernet.decrypt(self._env_vars.encode()).decode()
            return json.loads(decrypted)
        return []

    @env_vars.setter
    def env_vars(self, value: list[dict[str, str]] | None):
        json_str = json.dumps(value or [])
        fernet = get_fernet()
        self._env_vars = fernet.encrypt(json_str.encode()).decode()

    @property
    def slug(self) -> str:
        return f"{self.project.slug}-id-{self.id[:7]}"

    @property
    def hostname(self) -> str:
        settings = get_settings()
        return f"{self.slug}.{settings.deploy_domain}"

    @property
    def url(self) -> str:
        settings = get_settings()
        return f"{settings.url_scheme}://{self.hostname}"

    def __repr__(self):
        return f"<Deployment {self.id}>"

    def parse_logs(self):
        """Parse raw build logs into structured format."""
        if not self.build_logs:
            return []

        return [parse_log(log) for log in self.build_logs.splitlines()]

    @property
    def parsed_logs(self):
        return self.parse_logs()


class Alias(Base):
    __tablename__: str = "alias"

    id: Mapped[int] = mapped_column(primary_key=True)
    subdomain: Mapped[str] = mapped_column(String(63), nullable=False, unique=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployment.id"), index=True)
    previous_deployment_id: Mapped[str | None] = mapped_column(
        ForeignKey("deployment.id"), index=True, nullable=True
    )
    type: Mapped[str] = mapped_column(
        SQLAEnum("branch", "environment", "environment_id", name="alias_type"),
        nullable=False,
    )
    value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        index=True, nullable=False, default=utc_now, onupdate=utc_now
    )

    deployment: Mapped[Deployment] = relationship(
        foreign_keys=[deployment_id], back_populates="aliases"
    )
    previous_deployment: Mapped[Deployment] = relationship(
        foreign_keys=[previous_deployment_id]
    )

    @property
    def hostname(self) -> str:
        settings = get_settings()
        return f"{self.subdomain}.{settings.deploy_domain}"

    @property
    def url(self) -> str:
        settings = get_settings()
        return f"{settings.url_scheme}://{self.hostname}"

    @classmethod
    async def update_or_create(
        cls,
        db: AsyncSession,
        subdomain: str,
        deployment_id: str,
        type: str,
        value: str | None = None,
        environment_id: str | None = None,
    ) -> dict[str, object]:
        """Update or create alias"""
        result_query = await db.execute(select(cls).where(cls.subdomain == subdomain))
        alias = result_query.scalar_one_or_none()

        result = {}
        result["alias"] = None

        if alias:
            if alias.deployment_id == deployment_id:
                result["alias"] = alias
                return result

            if type == "environment" and environment_id == "prod":
                alias.previous_deployment_id = alias.deployment_id
            else:
                alias.previous_deployment_id = None
            alias.deployment_id = deployment_id
        else:
            alias = cls(
                subdomain=subdomain,
                deployment_id=deployment_id,
                type=type,
                value=value,
            )
            db.add(alias)

        result["alias"] = alias
        return result

    @override
    def __repr__(self):
        return f"<Alias {self.subdomain}>"
