"""Ask GitHub's open API about a repository.

Only one fixed host is ever contacted, and the owner/name are checked against a strict
pattern first, so a user cannot make the server fetch an arbitrary address.
"""

import re
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

GITHUB_API = "https://api.github.com"
NAME_PART = r"[A-Za-z0-9_.-]{1,100}"
FULL_NAME_RE = re.compile(rf"^({NAME_PART})/({NAME_PART})$")


class RepoLookupError(Exception):
    """kind: not_found | rate_limited | unavailable"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class GitHubRepo:
    github_id: int
    owner: str
    name: str
    default_branch: str
    is_private: bool


def parse_full_name(text: str) -> tuple[str, str]:
    """Accepts 'owner/repo' or a pasted https://github.com/owner/repo link."""
    cleaned = text.strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    cleaned = cleaned.removesuffix("/").removesuffix(".git")
    match = FULL_NAME_RE.match(cleaned)
    if not match:
        raise ValueError("Enter the repository as owner/name, for example psf/requests.")
    return match.group(1), match.group(2)


def lookup_repo(owner: str, name: str) -> GitHubRepo:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "prsentinel"}
    token = get_settings().github_token
    if token is not None:
        headers["Authorization"] = f"Bearer {token.get_secret_value()}"
    try:
        response = httpx.get(
            f"{GITHUB_API}/repos/{owner}/{name}", headers=headers, timeout=10.0
        )
    except httpx.HTTPError:
        raise RepoLookupError("unavailable", "Could not reach GitHub. Try again.") from None

    if response.status_code == 404:
        raise RepoLookupError("not_found", "Repository not found, or it is not public.")
    if response.status_code in (403, 429):
        raise RepoLookupError("rate_limited", "GitHub is limiting lookups. Try again later.")
    if response.status_code != 200:
        raise RepoLookupError("unavailable", "GitHub returned an unexpected answer.")

    data = response.json()
    full_name = str(data.get("full_name", f"{owner}/{name}"))
    real_owner, _, real_name = full_name.partition("/")
    return GitHubRepo(
        github_id=int(data["id"]),
        owner=real_owner,
        name=real_name,
        default_branch=str(data.get("default_branch") or "main"),
        is_private=bool(data.get("private", False)),
    )


def get_repo_lookup():
    """FastAPI dependency. Tests replace it so no real network call is made."""
    return lookup_repo
