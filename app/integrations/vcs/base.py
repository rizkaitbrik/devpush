from __future__ import annotations

from typing import Protocol

from .models import Account, Branch, Commit, GitTree, OAuthTokens, Repository, UserInfo


class VcsAdapter(Protocol):
    async def get_oauth_tokens(self, code: str) -> OAuthTokens:
        """Exchange authorization code for OAuth tokens."""
        ...

    async def get_user_info(self, access_token: str) -> UserInfo:
        """Fetch user identity including all emails."""
        ...

    async def get_accounts(self, access_token: str) -> list[Account]:
        """List accounts/orgs the user granted access to (selected at connect time)."""
        ...

    async def get_repositories(
        self, access_token: str, account_id: str, query: str = ""
    ) -> list[Repository]:
        """List repositories for the selected account, optionally filtered by query."""
        ...

    async def get_repository(self, access_token: str, repo_id: int) -> Repository:
        """Fetch a single repository by its provider ID."""
        ...

    async def get_repository_branches(
        self, access_token: str, repo_id: int
    ) -> list[Branch]:
        """List branches for a repository."""
        ...

    async def get_repository_commits(
        self,
        access_token: str,
        repo_id: int,
        branch: str | None = None,
        search: str | None = None,
        per_page: int = 30,
        page: int = 1,
    ) -> list[Commit]:
        """List commits, optionally filtered by branch or search query."""
        ...

    async def get_repository_commit(
        self, access_token: str, repo_id: int, sha: str
    ) -> Commit:
        """Fetch a single commit by its SHA."""
        ...

    async def get_git_tree(
        self,
        access_token: str,
        repo_id: int,
        sha: str = "HEAD",
        recursive: bool = True,
    ) -> GitTree:
        """Fetch the file tree for a repository at a given ref."""
        ...

    async def get_file_content(
        self, access_token: str, repo_id: int, path: str, ref: str = "HEAD"
    ) -> str | None:
        """Fetch decoded file content at a given ref, or None if not found."""
        ...
