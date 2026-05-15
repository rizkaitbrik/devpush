from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import VcsInstallation
from integrations.vcs.gitlab.adapter import GitLabAdapter

logger = logging.getLogger(__name__)


def _is_token_expired(installation: VcsInstallation) -> bool:
    if not installation.token_expires_at:
        return False
    return installation.token_expires_at <= datetime.now(timezone.utc).replace(tzinfo=None)


class GitLabInstallationService:
    def __init__(self, adapter: GitLabAdapter):
        self.adapter = adapter

    async def get_or_create(
        self,
        access_token: str,
        provider_account_id: str,
        provider_account_name: str,
        db: AsyncSession,
        refresh_token: str | None = None,
        token_expires_at: datetime | None = None,
    ) -> VcsInstallation:
        """Find or create a VcsInstallation for a GitLab namespace."""
        result = await db.execute(
            select(VcsInstallation).where(
                VcsInstallation.provider == "gitlab",
                VcsInstallation.provider_account_id == provider_account_id,
            )
        )
        installation = result.scalar_one_or_none()

        if not installation:
            installation = VcsInstallation(
                provider="gitlab",
                provider_account_id=provider_account_id,
                provider_account_name=provider_account_name,
            )

        installation.token = access_token
        if refresh_token:
            installation.refresh_token = refresh_token
        if token_expires_at:
            installation.token_expires_at = token_expires_at

        installation = await db.merge(installation)
        await db.commit()
        return installation

    async def refresh(self, vcs_installation_id: str, db: AsyncSession) -> VcsInstallation:
        """Load a VcsInstallation and refresh its token if expired."""
        result = await db.execute(
            select(VcsInstallation).where(VcsInstallation.id == vcs_installation_id)
        )
        installation = result.scalar_one_or_none()
        if not installation:
            raise ValueError(f"VcsInstallation {vcs_installation_id!r} not found")

        if not installation.token or _is_token_expired(installation):
            if not installation.refresh_token:
                raise ValueError(
                    f"GitLab installation {vcs_installation_id!r} has no refresh token; "
                    "the user must reconnect their GitLab account."
                )
            logger.info("Refreshing expired GitLab token for installation %s", vcs_installation_id)
            tokens = await self.adapter.refresh_access_token(installation.refresh_token)
            installation.token = tokens.access_token
            if tokens.refresh_token:
                installation.refresh_token = tokens.refresh_token
            if tokens.expires_at:
                installation.token_expires_at = tokens.expires_at
            await db.commit()

        return installation
