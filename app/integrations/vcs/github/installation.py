from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from integrations.vcs.github.adapter import GitHubAdapter
from db.models import VcsInstallation


def _is_token_expired(installation: VcsInstallation) -> bool:
    if not installation.token_expires_at:
        return False
    expires = installation.token_expires_at.replace(tzinfo=None)
    return expires <= datetime.now(timezone.utc).replace(tzinfo=None)


class GitHubInstallationService:
    def __init__(self, adapter: GitHubAdapter):
        self.adapter = adapter

    async def get_or_refresh_installation(
        self, github_installation_id: int, db: AsyncSession
    ) -> VcsInstallation:
        """Find or create a VcsInstallation by GitHub App installation ID and refresh its token."""
        result = await db.execute(
            select(VcsInstallation).where(
                VcsInstallation.installation_id == github_installation_id,
                VcsInstallation.provider == "github",
            )
        )
        installation = result.scalar_one_or_none()

        if not installation:
            installation = VcsInstallation(
                installation_id=github_installation_id,
                provider="github",
                provider_account_id=str(github_installation_id),
            )

        if not installation.token or _is_token_expired(installation):
            token_data = await self.adapter.get_installation_access_token(
                str(github_installation_id)
            )
            installation.token = token_data["token"]
            installation.token_expires_at = datetime.fromisoformat(
                token_data["expires_at"].replace("Z", "+00:00")
            ).replace(tzinfo=None)

        installation = await db.merge(installation)
        await db.commit()
        return installation

    async def refresh(self, vcs_installation_id: str, db: AsyncSession) -> VcsInstallation:
        """Load a VcsInstallation by its PK and always fetch a fresh token."""
        result = await db.execute(
            select(VcsInstallation).where(VcsInstallation.id == vcs_installation_id)
        )
        installation = result.scalar_one_or_none()
        if not installation:
            raise ValueError(f"VcsInstallation {vcs_installation_id!r} not found")

        token_data = await self.adapter.get_installation_access_token(
            str(installation.installation_id)
        )
        installation.token = token_data["token"]
        installation.token_expires_at = datetime.fromisoformat(
            token_data["expires_at"].replace("Z", "+00:00")
        ).replace(tzinfo=None)
        await db.commit()

        return installation

    async def get_token_for_repo(self, repo_full_name: str) -> str:
        """Get a fresh installation token scoped to the installation that covers this repo."""
        repo_installation = await self.adapter.get_repository_installation(repo_full_name)
        installation_id = str(repo_installation["id"])
        token_data = await self.adapter.get_installation_access_token(installation_id)
        return token_data["token"]
