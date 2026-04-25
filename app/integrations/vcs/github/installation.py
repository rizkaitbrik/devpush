from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from integrations.vcs.github.adapter import GitHubAdapter
from models import GithubInstallation


class GitHubInstallationService:
    def __init__(self, adapter: GitHubAdapter):
        self.adapter = adapter

    async def get_or_refresh_installation(
        self, installation_id: int, db: AsyncSession
    ) -> GithubInstallation:
        result = await db.execute(
            select(GithubInstallation).where(
                GithubInstallation.installation_id == installation_id
            )
        )
        installation = result.scalar_one_or_none()

        if not installation:
            installation = GithubInstallation(installation_id=installation_id)

        token_expired = installation.token_expires_at and installation.token_expires_at <= datetime.now(timezone.utc).replace(tzinfo=None)
        if not installation.token or token_expired:
            token_data = await self.adapter.get_installation_access_token(
                str(installation_id)
            )
            installation.token = token_data["token"]
            installation.token_expires_at = datetime.fromisoformat(
                token_data["expires_at"].replace("Z", "+00:00")
            ).replace(tzinfo=None)

        installation = await db.merge(installation)
        await db.commit()
        return installation
