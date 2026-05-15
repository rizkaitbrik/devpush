from __future__ import annotations

import time
import urllib.parse
from datetime import datetime, timezone

import httpx

from integrations.vcs.models import (
    Account,
    AccountType,
    Branch,
    Commit,
    CommitAuthor,
    EmailType,
    GitTree,
    GitTreeItem,
    GitTreeType,
    OAuthTokens,
    Repository,
    RepositoryOwner,
    UserInfo,
)

GITLAB_API = "https://gitlab.com/api/v4"
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class RateLimitError(Exception):
    def __init__(self, retry_after: int | None = None):
        self.retry_after = retry_after
        super().__init__(f"GitLab rate limit exceeded. Retry after {retry_after}s.")


def _check_rate_limit(response: httpx.Response) -> None:
    if response.status_code == 429:
        retry_after = None
        if "Retry-After" in response.headers:
            try:
                retry_after = int(response.headers["Retry-After"])
            except ValueError:
                pass
        raise RateLimitError(retry_after=retry_after)


class GitLabAdapter:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    def _headers(self, access_token: str) -> dict:
        return {"Authorization": f"Bearer {access_token}"}

    async def get_oauth_tokens(self, code: str, redirect_uri: str) -> OAuthTokens:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            response = await client.post(
                "https://gitlab.com/oauth/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
        response.raise_for_status()
        data = response.json()
        expires_at = None
        if "expires_in" in data:
            created_at = data.get("created_at", int(time.time()))
            expires_at = datetime.fromtimestamp(
                created_at + data["expires_in"], tz=timezone.utc
            ).replace(tzinfo=None)
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
        )

    async def refresh_access_token(self, refresh_token: str) -> OAuthTokens:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            response = await client.post(
                "https://gitlab.com/oauth/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        response.raise_for_status()
        data = response.json()
        expires_at = None
        if "expires_in" in data:
            created_at = data.get("created_at", int(time.time()))
            expires_at = datetime.fromtimestamp(
                created_at + data["expires_in"], tz=timezone.utc
            ).replace(tzinfo=None)
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
        )

    async def get_user_info(self, access_token: str) -> UserInfo:
        headers = self._headers(access_token)
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            user_resp = await client.get(f"{GITLAB_API}/user", headers=headers)
            _check_rate_limit(user_resp)
            user_resp.raise_for_status()
            user = user_resp.json()

            emails_resp = await client.get(f"{GITLAB_API}/user/emails", headers=headers)
            _check_rate_limit(emails_resp)

        emails: dict[str, EmailType] = {}
        if emails_resp.is_success:
            for entry in emails_resp.json():
                emails[entry["email"]] = EmailType.SECONDARY
        primary = user.get("email")
        if primary:
            emails[primary] = EmailType.PRIMARY

        return UserInfo(
            id=str(user["id"]),
            username=user["username"],
            name=user.get("name"),
            avatar_url=user.get("avatar_url"),
            verified=bool(user.get("confirmed_at")),
            emails=emails,
        )

    async def get_accounts(self, access_token: str) -> list[Account]:
        headers = self._headers(access_token)
        accounts: list[Account] = []

        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            user_resp = await client.get(f"{GITLAB_API}/user", headers=headers)
            _check_rate_limit(user_resp)
            if user_resp.is_success:
                user = user_resp.json()
                accounts.append(Account(
                    id=str(user["id"]),
                    name=user["username"],
                    type=AccountType.USER,
                ))

            page = 1
            while True:
                groups_resp = await client.get(
                    f"{GITLAB_API}/groups",
                    headers=headers,
                    params={"min_access_level": 40, "per_page": 100, "page": page},
                )
                _check_rate_limit(groups_resp)
                if not groups_resp.is_success:
                    break
                groups = groups_resp.json()
                if not groups:
                    break
                for g in groups:
                    accounts.append(Account(
                        id=str(g["id"]),
                        name=g["path"],
                        type=AccountType.ORGANIZATION,
                    ))
                next_page = groups_resp.headers.get("X-Next-Page")
                if not next_page:
                    break
                page += 1

        return accounts

    async def get_repositories(
        self, access_token: str, account_id: str, query: str = ""
    ) -> list[Repository]:
        headers = self._headers(access_token)

        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            user_resp = await client.get(f"{GITLAB_API}/user", headers=headers)
            _check_rate_limit(user_resp)
            is_personal = user_resp.is_success and str(user_resp.json()["id"]) == account_id

            repos: list[Repository] = []
            page = 1

            if is_personal:
                params: dict = {
                    "membership": "true",
                    "min_access_level": 30,
                    "per_page": 50,
                    "page": page,
                    "owned": "true",
                }
                if query:
                    params["search"] = query
                while True:
                    resp = await client.get(f"{GITLAB_API}/projects", headers=headers, params=params)
                    _check_rate_limit(resp)
                    if not resp.is_success:
                        break
                    batch = resp.json()
                    repos.extend(_parse_project(p) for p in batch if p.get("permissions"))
                    next_page = resp.headers.get("X-Next-Page")
                    if not next_page:
                        break
                    page += 1
                    params["page"] = page
            else:
                params = {
                    "min_access_level": 30,
                    "per_page": 50,
                    "page": page,
                    "with_shared": "false",
                }
                if query:
                    params["search"] = query
                while True:
                    resp = await client.get(
                        f"{GITLAB_API}/groups/{account_id}/projects",
                        headers=headers,
                        params=params,
                    )
                    _check_rate_limit(resp)
                    if not resp.is_success:
                        break
                    batch = resp.json()
                    repos.extend(_parse_project(p) for p in batch)
                    next_page = resp.headers.get("X-Next-Page")
                    if not next_page:
                        break
                    page += 1
                    params["page"] = page

        return repos

    async def get_repository(self, access_token: str, repo_id: int) -> Repository:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{GITLAB_API}/projects/{repo_id}",
                headers=self._headers(access_token),
            )
        _check_rate_limit(resp)
        resp.raise_for_status()
        return _parse_project(resp.json())

    async def get_repository_branches(
        self, access_token: str, repo_id: int
    ) -> list[Branch]:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{GITLAB_API}/projects/{repo_id}/repository/branches",
                headers=self._headers(access_token),
                params={"per_page": 100},
            )
        _check_rate_limit(resp)
        resp.raise_for_status()
        return [Branch(name=b["name"], sha=b["commit"]["id"]) for b in resp.json()]

    async def get_repository_commits(
        self,
        access_token: str,
        repo_id: int,
        branch: str | None = None,
        search: str | None = None,
        per_page: int = 30,
        page: int = 1,
    ) -> list[Commit]:
        params: dict = {"per_page": per_page, "page": page}
        if branch:
            params["ref_name"] = branch
        if search:
            params["search"] = search
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{GITLAB_API}/projects/{repo_id}/repository/commits",
                headers=self._headers(access_token),
                params=params,
            )
        _check_rate_limit(resp)
        resp.raise_for_status()
        return [_parse_commit(c) for c in resp.json()]

    async def get_repository_commit(
        self, access_token: str, repo_id: int, sha: str
    ) -> Commit:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{GITLAB_API}/projects/{repo_id}/repository/commits/{sha}",
                headers=self._headers(access_token),
            )
        _check_rate_limit(resp)
        resp.raise_for_status()
        return _parse_commit(resp.json())

    async def get_git_tree(
        self,
        access_token: str,
        repo_id: int,
        sha: str = "HEAD",
        recursive: bool = True,
    ) -> GitTree:
        params: dict = {"per_page": 100, "recursive": str(recursive).lower()}
        if sha != "HEAD":
            params["ref"] = sha
        items: list[GitTreeItem] = []
        page = 1
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            while True:
                params["page"] = page
                resp = await client.get(
                    f"{GITLAB_API}/projects/{repo_id}/repository/tree",
                    headers=self._headers(access_token),
                    params=params,
                )
                _check_rate_limit(resp)
                if not resp.is_success:
                    break
                batch = resp.json()
                for item in batch:
                    item_type = GitTreeType.TREE if item["type"] == "tree" else GitTreeType.BLOB
                    items.append(GitTreeItem(path=item["path"], type=item_type, sha=item["id"]))
                next_page = resp.headers.get("X-Next-Page")
                if not next_page:
                    break
                page += 1
        return GitTree(sha=sha, items=items)

    async def get_file_content(
        self, access_token: str, repo_id: int, path: str, ref: str = "HEAD"
    ) -> str | None:
        encoded_path = urllib.parse.quote(path, safe="")
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{GITLAB_API}/projects/{repo_id}/repository/files/{encoded_path}/raw",
                headers=self._headers(access_token),
                params={"ref": ref},
            )
        if resp.status_code == 404:
            return None
        _check_rate_limit(resp)
        resp.raise_for_status()
        return resp.text

    async def register_webhook(
        self,
        access_token: str,
        repo_id: int,
        webhook_url: str,
        secret_token: str,
    ) -> int:
        """Register a push webhook on a GitLab project. Returns the hook ID."""
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{GITLAB_API}/projects/{repo_id}/hooks",
                headers=self._headers(access_token),
                json={
                    "url": webhook_url,
                    "token": secret_token,
                    "push_events": True,
                    "tag_push_events": False,
                    "issues_events": False,
                    "merge_requests_events": False,
                    "note_events": False,
                    "pipeline_events": False,
                    "wiki_page_events": False,
                    "enable_ssl_verification": True,
                },
            )
        _check_rate_limit(resp)
        resp.raise_for_status()
        return resp.json()["id"]

    async def delete_webhook(
        self, access_token: str, repo_id: int, hook_id: int
    ) -> None:
        """Delete a webhook from a GitLab project."""
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.delete(
                f"{GITLAB_API}/projects/{repo_id}/hooks/{hook_id}",
                headers=self._headers(access_token),
            )
        if resp.status_code == 404:
            return
        _check_rate_limit(resp)
        resp.raise_for_status()

    async def get_namespace_by_path(
        self, access_token: str, path: str
    ) -> dict | None:
        """Look up a namespace (user or group) by its URL path."""
        encoded = urllib.parse.quote(path, safe="")
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{GITLAB_API}/namespaces/{encoded}",
                headers=self._headers(access_token),
            )
        if resp.status_code == 404:
            return None
        _check_rate_limit(resp)
        resp.raise_for_status()
        return resp.json()


def _parse_project(p: dict) -> Repository:
    ns = p.get("namespace", {})
    owner = RepositoryOwner(login=ns.get("path", ""))
    perms = p.get("permissions") or {}
    proj_access = (perms.get("project_access") or {}).get("access_level", 0)
    group_access = (perms.get("group_access") or {}).get("access_level", 0)
    can_push = max(proj_access, group_access) >= 30
    return Repository(
        id=p["id"],
        name=p["path"],
        full_name=p["path_with_namespace"],
        description=p.get("description"),
        can_push=can_push,
        private=p.get("visibility") != "public",
        default_branch=p.get("default_branch") or "main",
        updated_at=p.get("last_activity_at"),
        html_url=p.get("web_url", ""),
        owner=owner,
    )


def _parse_commit(c: dict) -> Commit:
    author = CommitAuthor(
        name=c.get("author_name", ""),
        date=c.get("authored_date", c.get("created_at", "")),
        login=None,
    )
    return Commit(sha=c["id"], message=c.get("title", c.get("message", "")), author=author)
