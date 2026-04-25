from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AccountType(Enum):
    USER = "user"
    ORGANIZATION = "organization"


class GitTreeType(Enum):
    BLOB = "blob"
    TREE = "tree"


class EmailType(Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str | None = None


@dataclass
class UserInfo:
    id: str
    username: str
    emails: dict[str, EmailType]
    avatar_url: str | None
    verified: bool
    name: str | None = None


@dataclass
class Account:
    id: str
    name: str
    type: AccountType


@dataclass
class RepositoryOwner:
    login: str


@dataclass
class Repository:
    id: int
    name: str
    full_name: str
    description: str | None = None
    can_push: bool = False
    private: bool = False
    default_branch: str | None = None
    updated_at: str | None = None
    html_url: str | None = None
    owner: RepositoryOwner | None = None


@dataclass
class Branch:
    name: str
    sha: str


@dataclass
class CommitAuthor:
    name: str
    date: str
    login: str | None = None


@dataclass
class Commit:
    sha: str
    message: str
    author: CommitAuthor


@dataclass
class GitTreeItem:
    path: str
    type: GitTreeType
    sha: str


@dataclass
class GitTree:
    sha: str
    items: list[GitTreeItem] = field(default_factory=list)