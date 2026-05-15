from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

from cryptography.fernet import Fernet

from config import get_settings

FORBIDDEN_TEAM_SLUGS = [
    "admin",
    "api",
    "assets",
    "auth",
    "deployment-not-found",
    "health",
    "new-team",
    "setup",
    "upload",
    "user",
]


def utc_now() -> datetime:
    """Get current UTC time as timezone-naive datetime"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@lru_cache
def get_fernet() -> Fernet:
    """Get Fernet instance using encryption key from settings"""
    settings = get_settings()
    return Fernet(settings.encryption_key)
