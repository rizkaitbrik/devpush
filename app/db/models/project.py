from __future__ import annotations

import json
import re
from datetime import datetime
from secrets import token_hex
from typing import TYPE_CHECKING, override

from sqlalchemy import (
    BigInteger,
    Boolean,
    Enum as SQLAEnum,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config import get_settings
from db import Base
from db.models._utils import get_fernet, utc_now
from db.models.team import Team
from db.models.user import User
from db.models.vcs_installation import VcsInstallation
from utils.color import get_color

if TYPE_CHECKING:
    from db.models.deployment import Alias, Deployment
    from db.models.domain import Domain
    from db.models.storage import Storage, StorageProject


class Project(Base):
    __tablename__: str = "project"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: token_hex(16)
    )
    name: Mapped[str] = mapped_column(String(100), index=True)
    has_avatar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    repo_status: Mapped[str] = mapped_column(
        SQLAEnum(
            "active", "deleted", "removed", "transferred", name="project_vcs_status"
        ),
        nullable=False,
        default="active",
    )
    vcs_installation_id: Mapped[str] = mapped_column(
        ForeignKey("vcs_installation.id"), nullable=False, index=True
    )
    environments: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    _env_vars: Mapped[str] = mapped_column("env_vars", Text, nullable=False, default="")
    slug: Mapped[str] = mapped_column(String(40), nullable=True, unique=True)
    config: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
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
    status: Mapped[str] = mapped_column(
        SQLAEnum("active", "paused", "deleted", name="project_status"),
        nullable=False,
        default="active",
    )
    team_id: Mapped[str] = mapped_column(ForeignKey("team.id"), index=True)

    vcs_installation: Mapped[VcsInstallation] = relationship(back_populates="projects")
    deployments: Mapped[list["Deployment"]] = relationship(back_populates="project")
    team: Mapped[Team] = relationship(back_populates="projects")
    created_by_user: Mapped[User | None] = relationship(
        foreign_keys=[created_by_user_id]
    )
    domains: Mapped[list["Domain"]] = relationship(back_populates="project")
    storage_links: Mapped[list["StorageProject"]] = relationship(
        back_populates="project"
    )

    __table_args__ = (
        UniqueConstraint("team_id", "name", name="uq_project_team_name"),
        Index(
            "ix_project_team_name_lower",
            "team_id",
            func.lower(name),
            unique=True,
        ),
    )

    @property
    def env_vars(self) -> list[dict[str, str]]:
        if self._env_vars:
            fernet = get_fernet()
            decrypted = fernet.decrypt(self._env_vars.encode()).decode()
            return json.loads(decrypted)
        return []

    @env_vars.setter
    def env_vars(self, value: list[dict[str, str]]):
        json_str = json.dumps(value or [])
        fernet = get_fernet()
        self._env_vars = fernet.encrypt(json_str.encode()).decode()

    @property
    def hostname(self) -> str:
        settings = get_settings()
        return f"{self.slug}.{settings.deploy_domain}"

    @property
    def url(self) -> str:
        settings = get_settings()
        return f"{settings.url_scheme}://{self.hostname}"

    @property
    def color(self) -> str:
        return get_color(self.id)

    @override
    def __repr__(self):
        return f"<Project {self.name}>"

    def get_env_vars(self, environment: str) -> list[dict[str, str]]:
        """Flattened env vars for a specific environment."""
        env_vars = [var for var in self.env_vars if not var.get("environment")]
        for var in self.env_vars:
            if var.get("environment") == environment:
                env_vars = [v for v in env_vars if v["key"] != var["key"]]
                env_vars.append(var)
        return env_vars

    def has_active_environment_with_slug(
        self, slug: str, exclude_id: str | None = None
    ) -> bool:
        """Check if an active environment with given slug exists"""
        return any(
            environment
            for environment in self.active_environments
            if environment.get("slug") == slug
            and (exclude_id is None or environment.get("id") != exclude_id)
        )

    def create_environment(self, name: str, slug: str, **kwargs) -> dict:
        """Create a new environment with a unique ID"""
        if self.has_active_environment_with_slug(slug):
            raise ValueError(f"An active environment with slug '{slug}' already exists")

        env = {
            "id": token_hex(4),
            "name": name,
            "slug": slug,
            "status": "active",
            **kwargs,
        }
        environments = self.environments.copy()
        environments.append(env)
        self.environments = environments
        return env

    def update_environment(self, environment_id: str, values: dict) -> dict | None:
        """Update environment"""
        env = self.get_environment_by_id(environment_id)
        if not env:
            return None

        if environment_id == "prod" and (
            env.get("name") != values.get("name")
            or env.get("slug") != values.get("slug")
        ):
            raise ValueError("Cannot modify production environment")

        new_slug = values.get("slug")
        if (
            new_slug
            and new_slug != env.get("slug")
            and self.has_active_environment_with_slug(
                new_slug, exclude_id=environment_id
            )
        ):
            raise ValueError(
                f"An active environment with slug '{new_slug}' already exists"
            )

        env_index = next(
            i for i, e in enumerate(self.environments) if e["id"] == environment_id
        )
        old_slug = self.environments[env_index]["slug"]

        environments = self.environments.copy()
        environments[env_index] = {**environments[env_index], **values}
        self.environments = environments

        if new_slug and new_slug != old_slug:
            env_vars = self.env_vars.copy()
            for var in env_vars:
                if var.get("environment") == old_slug:
                    var["environment"] = new_slug
            self.env_vars = env_vars

        return environments[env_index]

    def delete_environment(self, environment_id: str | None) -> bool:
        """Soft delete environment"""
        if not environment_id:
            return False

        if environment_id == "prod":
            raise ValueError("Cannot delete production environment")

        env = self.get_environment_by_id(environment_id)
        if not env:
            return False

        env_vars = self.env_vars.copy()
        env_vars = [var for var in env_vars if var.get("environment") != env["slug"]]
        self.env_vars = env_vars

        env_index = next(
            i for i, e in enumerate(self.environments) if e["id"] == environment_id
        )
        environments = self.environments.copy()
        environments[env_index] = {**environments[env_index], "status": "deleted"}
        self.environments = environments
        return True

    @property
    def active_environments(self) -> list[dict]:
        """Get only active environments"""
        return [env for env in self.environments if env.get("status") == "active"]

    def get_environment_by_id(self, env_id: str) -> dict | None:
        """Get environment by ID"""
        return next((env for env in self.environments if env["id"] == env_id), None)

    def get_environment_by_slug(
        self, slug: str, active_only: bool = True
    ) -> dict | None:
        """Get environment by slug"""
        environments = self.active_environments if active_only else self.environments
        return next((env for env in environments if env["slug"] == slug), None)

    @property
    def storages(self) -> list["Storage"]:
        return [
            link.storage
            for link in self.storage_links
            if link.storage and link.storage.type == "database"
        ]

    async def get_domain_by_id(self, db: AsyncSession, domain_id: int) -> dict | None:
        """Get domain by ID"""
        from db.models.domain import Domain
        result = await db.execute(
            select(Domain).where(
                Domain.id == domain_id,
                Domain.project_id == self.id,
            )
        )
        return result.scalar_one_or_none()

    async def get_domain_by_hostname(
        self, db: AsyncSession, hostname: str
    ) -> dict | None:
        """Get domain by hostname"""
        from db.models.domain import Domain
        result = await db.execute(
            select(Domain).where(
                Domain.hostname == hostname,
                Domain.project_id == self.id,
            )
        )
        return result.scalar_one_or_none()

    async def get_environment_aliases(self, db: AsyncSession) -> dict[str, "Alias"]:
        """Get environment aliases for this project"""
        from db.models.deployment import Alias, Deployment
        result = await db.execute(
            select(Alias)
            .join(Deployment, Alias.deployment_id == Deployment.id)
            .where(Deployment.project_id == self.id, Alias.type == "environment")
        )
        aliases = result.scalars().all()
        return {alias.value: alias for alias in aliases}

    def get_environment_hostname(self, environment_slug: str) -> str:
        """Get environment hostname"""
        settings = get_settings()
        if environment_slug == "production":
            return self.hostname
        return f"{self.slug}-env-{environment_slug}.{settings.deploy_domain}"

    def get_environment_url(self, environment_slug: str) -> str:
        """Get environment URL"""
        settings = get_settings()
        return (
            f"{settings.url_scheme}://{self.get_environment_hostname(environment_slug)}"
        )

    def get_branch_hostname(self, branch: str) -> str:
        """Get branch hostname"""
        settings = get_settings()
        return f"{self.slug}-branch-{branch}.{settings.deploy_domain}"

    def get_branch_url(self, branch: str) -> str:
        """Get branch URL"""
        settings = get_settings()
        return f"{settings.url_scheme}://{self.get_branch_hostname(branch)}"


@event.listens_for(Project, "after_insert")
def set_project_slug(mapper, connection, project):
    """Generate and set slug after project is inserted (and has an ID)."""
    if not project.slug:
        team_slug = connection.scalar(
            select(Team.slug).where(Team.id == project.team_id)
        )
        base_slug = f"{project.name}-{team_slug}".lower()
        base_slug = base_slug.replace(" ", "-").replace("_", "-").replace(".", "-")
        base_slug = re.sub(r"[^a-z0-9-]", "", base_slug)
        base_slug = re.sub(r"-+", "-", base_slug)
        base_slug = base_slug[:40].strip("-")
        if not base_slug:
            base_slug = f"project-{project.id}"[:40]

        new_slug = (
            base_slug
            if not connection.scalar(
                select(Project.slug).where(Project.slug == base_slug)
            )
            else f"{base_slug[:32]}-{str(project.id)[:7]}"
        )

        connection.execute(
            update(Project).where(Project.id == project.id).values(slug=new_slug)
        )
        project.slug = new_slug
