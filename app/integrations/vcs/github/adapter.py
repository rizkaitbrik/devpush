from __future__ import annotations

import base64
import time
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import jwt

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


class GitHubAdapter:
    def __init__(
        self, client_id: str, client_secret: str, app_id: str, private_key: str
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.app_id = app_id
        self.private_key = private_key
        self._jwt_token: str | None = None
        self._jwt_expires_at: int | None = None
        self._time_offset: int | None = None
        self._time_offset_checked_at: int | None = None

    def _check_time_offset(self) -> int:
        now = int(time.time())
        if (
            self._time_offset is not None
            and self._time_offset_checked_at is not None
            and now - self._time_offset_checked_at < 3600
        ):
            return self._time_offset

        try:
            response = httpx.get("https://api.github.com", timeout=5.0)
            if "Date" in response.headers:
                github_time = parsedate_to_datetime(response.headers["Date"])
                self._time_offset = int(github_time.timestamp()) - now
                self._time_offset_checked_at = now
                return self._time_offset
        except Exception:
            pass

        return self._time_offset or 0

    @property
    def jwt_token(self) -> str:
        now = int(time.time())
        if (
            not self._jwt_token
            or not self._jwt_expires_at
            or self._jwt_expires_at - now < 60
        ):
            offset = self._check_time_offset()
            adjusted_now = now + offset
            self._jwt_expires_at = now + (10 * 60)
            self._jwt_token = jwt.encode(
                {"iat": adjusted_now, "exp": adjusted_now + (10 * 60), "iss": self.app_id},
                self.private_key,
                algorithm="RS256",
            )
        return self._jwt_token


    async def get_oauth_tokens(self, code: str) -> OAuthTokens:
        response = httpx.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
            },
        )
        response.raise_for_status()
        data = response.json()
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
        )

    async def get_user_info(self, access_token: str) -> UserInfo:
        headers = {"Authorization": f"Bearer {access_token}"}

        user_resp = httpx.get("https://api.github.com/user", headers=headers)
        user_resp.raise_for_status()
        user = user_resp.json()

        emails_resp = httpx.get("https://api.github.com/user/emails", headers=headers)
        emails_resp.raise_for_status()

        emails: dict[str, EmailType] = {
            entry["email"]: (
                EmailType.PRIMARY if entry.get("primary") else EmailType.SECONDARY
            )
            for entry in emails_resp.json()
            if entry.get("verified")
        }

        return UserInfo(
            id=str(user["id"]),
            username=user["login"],
            name=user.get("name"),
            avatar_url=user.get("avatar_url"),
            verified=bool(emails),
            emails=emails,
        )

    @staticmethod
    async def get_accounts(access_token: str) -> list[Account]:
        installations: list[dict] = []
        page = 1
        per_page = 100
        total_count = None

        while True:
            resp = httpx.get(
                "https://api.github.com/user/installations",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"per_page": per_page, "page": page},
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("installations", [])
            installations.extend(batch)
            total_count = data.get("total_count", total_count)

            if (
                not batch
                or (total_count is not None and page * per_page >= total_count)
                or len(batch) < per_page
            ):
                break
            page += 1

        return [
            Account(
                id=str(inst["id"]),
                name=inst["account"]["login"],
                type=(
                    AccountType.ORGANIZATION
                    if inst["account"]["type"] == "Organization"
                    else AccountType.USER
                ),
            )
            for inst in installations
        ]

    async def get_repositories(
        self, access_token: str, account_id: str, query: str = ""
    ) -> list[Repository]:
        installation_data = await self.get_installation(account_id)
        selection = installation_data.get("repository_selection")
        account_name = installation_data["account"]["login"]

        if selection == "selected":
            raw = self._list_installation_repos_for_user(access_token, int(account_id))
            if query:
                q = query.lower()
                raw = [
                    r
                    for r in raw
                    if q in r.get("name", "").lower()
                    or q in r.get("full_name", "").lower()
                ]
                raw = sorted(
                    raw,
                    key=lambda r: (
                        int(
                            r.get("name", "").lower() != q
                            and r.get("full_name", "").lower() != q
                        ),
                        int(
                            not r.get("name", "").lower().startswith(q)
                            and not r.get("full_name", "").lower().startswith(q)
                        ),
                        len(r.get("name", "")),
                    ),
                )
        else:
            resp = httpx.get(
                "https://api.github.com/search/repositories",
                params={
                    "q": f"{query} in:name org:{account_name} fork:true".strip(),
                    "per_page": 5,
                    "sort": "updated",
                    "order": "desc",
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            raw = resp.json()["items"]

        return [
            self._parse_repository(r)
            for r in raw
            if r.get("permissions", {}).get("push", False)
        ]

    @staticmethod
    def _parse_repository(r: dict) -> Repository:
        owner_data = r.get("owner") or {}
        return Repository(
            id=r["id"],
            name=r["name"],
            full_name=r["full_name"],
            description=r.get("description"),
            can_push=bool(r.get("permissions", {}).get("push", False)),
            private=bool(r.get("private", False)),
            default_branch=r.get("default_branch"),
            updated_at=r.get("updated_at"),
            html_url=r.get("html_url"),
            owner=RepositoryOwner(login=owner_data["login"]) if owner_data.get("login") else None,
        )

    @staticmethod
    def _list_installation_repos_for_user(
            access_token: str, installation_id: int
    ) -> list[dict]:
        repos: list[dict] = []
        page = 1
        per_page = 100
        total_count = None

        while True:
            resp = httpx.get(
                f"https://api.github.com/user/installations/{installation_id}/repositories",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"per_page": per_page, "page": page},
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("repositories", [])
            repos.extend(batch)
            total_count = data.get("total_count", total_count)

            if (
                not batch
                or (total_count is not None and page * per_page >= total_count)
                or len(batch) < per_page
            ):
                break
            page += 1

        return repos

    async def get_repository(self, access_token: str, repo_id: int) -> Repository:
        resp = httpx.get(
            f"https://api.github.com/repositories/{repo_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return self._parse_repository(resp.json())

    @staticmethod
    async def get_repository_branches(
            access_token: str, repo_id: int
    ) -> list[Branch]:
        resp = httpx.get(
            f"https://api.github.com/repositories/{repo_id}/branches",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return [Branch(name=b["name"], sha=b["commit"]["sha"]) for b in resp.json()]

    async def get_repository_commits(
        self,
        access_token: str,
        repo_id: int,
        branch: str | None = None,
        search: str | None = None,
        per_page: int = 30,
        page: int = 1,
    ) -> list[Commit]:
        params: dict[str, str] = {"page": str(page), "per_page": str(per_page)}
        if branch:
            params["sha"] = branch
        if search:
            params["q"] = search

        resp = httpx.get(
            f"https://api.github.com/repositories/{repo_id}/commits",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        resp.raise_for_status()
        return [self._parse_commit(c) for c in resp.json()]

    async def get_repository_commit(
        self, access_token: str, repo_id: int, sha: str
    ) -> Commit:
        resp = httpx.get(
            f"https://api.github.com/repositories/{repo_id}/commits/{sha}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return self._parse_commit(resp.json())

    def _parse_commit(self, data: dict) -> Commit:
        user_author = data.get("author") or {}
        user_committer = data.get("committer") or {}
        payload = data.get("commit") or {}
        payload_author = payload.get("author") or {}
        payload_committer = payload.get("committer") or {}

        login = user_author.get("login") or user_committer.get("login")
        name = (
            payload_author.get("name")
            or payload_committer.get("name")
            or login
            or ""
        )
        date = payload_author.get("date") or payload_committer.get("date") or ""
        return Commit(
            sha=data["sha"],
            message=payload.get("message") or "",
            author=CommitAuthor(name=name, date=date, login=login),
        )

    async def get_git_tree(
        self,
        access_token: str,
        repo_id: int,
        sha: str = "HEAD",
        recursive: bool = True,
    ) -> GitTree:
        url = f"https://api.github.com/repositories/{repo_id}/git/trees/{sha}"
        if recursive:
            url += "?recursive=1"

        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        items = [
            GitTreeItem(
                path=item["path"],
                type=GitTreeType.BLOB if item["type"] == "blob" else GitTreeType.TREE,
                sha=item["sha"],
            )
            for item in data.get("tree", [])
        ]
        return GitTree(sha=data["sha"], items=items)

    async def get_file_content(
        self, access_token: str, repo_id: int, path: str, ref: str = "HEAD"
    ) -> str | None:
        try:
            resp = httpx.get(
                f"https://api.github.com/repositories/{repo_id}/contents/{path}?ref={ref}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            resp.raise_for_status()
            return base64.b64decode(resp.json()["content"]).decode("utf-8")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_installation(self, installation_id: str) -> dict:
        resp = httpx.get(
            f"https://api.github.com/app/installations/{installation_id}",
            headers={"Authorization": f"Bearer {self.jwt_token}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_installation_access_token(
        self, installation_id: str
    ) -> dict[str, Any]:
        resp = httpx.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {self.jwt_token}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_repository_installation(self, repo_full_name: str) -> dict:
        resp = httpx.get(
            f"https://api.github.com/repos/{repo_full_name}/installation",
            headers={"Authorization": f"Bearer {self.jwt_token}"},
        )
        resp.raise_for_status()
        return resp.json()
