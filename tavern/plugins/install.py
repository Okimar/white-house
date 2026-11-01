"""Getting a plugin: writing its files into data/plugins/<id>/, and fetching it from GitHub.

A GitHub link may be the repository ("https://github.com/me/dice"), a branch or tag
(".../tree/v2"), or a folder of it (".../tree/main/plugins/dice"). The exact commit is remembered,
so "check for updates" compares it with the newest commit of the same branch. No git is needed
(Termux may not have it): the archive comes from codeload.github.com over HTTPS.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import httpx

from tavern.errors import AppError
from tavern.plugins.manifest import MAX_PLUGIN_BYTES, PluginError

GITHUB_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?:/tree/([^/?#]+)(?:/([^?#]+?))?)?/?(?:[?#].*)?$")
API = "https://api.github.com"
CODELOAD = "https://codeload.github.com"
TIMEOUT = 30.0


class GitHubError(AppError):
    code = "github_failed"
    status = 502


def parse_github(url: str) -> dict[str, str]:
    match = GITHUB_RE.match(url.strip())
    if not match:
        raise PluginError("Нужна ссылка на репозиторий GitHub, например https://github.com/автор/дополнение",
                          code="bad_url")
    owner, repo, ref, subdir = match.groups()
    return {"owner": owner, "repo": repo, "ref": ref or "", "subdir": (subdir or "").strip("/")}


def _client(transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, transport=transport,
                             headers={"User-Agent": "Tavern", "Accept": "application/vnd.github+json"})


def _check(response: httpx.Response, what: str) -> None:
    if response.status_code == 404:
        raise GitHubError(f"GitHub: {what} не найден (или репозиторий закрытый)", status=404)
    if response.status_code in (403, 429):
        raise GitHubError("GitHub временно ограничил запросы. Попробуйте через час.", status=429)
    if response.status_code >= 400:
        raise GitHubError(f"GitHub ответил ошибкой {response.status_code}")


async def latest(link: dict[str, str], transport: httpx.AsyncBaseTransport | None = None) -> dict[str, str]:
    """The branch (default when not given) and its newest commit."""
    try:
        async with _client(transport) as client:
            ref = link["ref"]
            if not ref:
                response = await client.get(f"{API}/repos/{link['owner']}/{link['repo']}")
                _check(response, "репозиторий")
                ref = response.json().get("default_branch") or "main"
            response = await client.get(f"{API}/repos/{link['owner']}/{link['repo']}/commits/{ref}")
            _check(response, f"ветка или версия {ref!r}")
            return {"ref": ref, "sha": str(response.json()["sha"])}
    except httpx.HTTPError as exc:
        raise GitHubError(f"Нет связи с GitHub: {exc}") from None
    except (ValueError, KeyError, TypeError):
        raise GitHubError("GitHub прислал непонятный ответ") from None


async def download(link: dict[str, str], sha: str, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    url = f"{CODELOAD}/{link['owner']}/{link['repo']}/zip/{sha}"
    try:
        async with _client(transport) as client, client.stream("GET", url) as response:
            _check(response, "архив")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_PLUGIN_BYTES * 3:  # the whole repository; the plugin itself is checked later
                    raise PluginError("Репозиторий слишком большой для дополнения", code="plugin_too_large")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise GitHubError(f"Нет связи с GitHub: {exc}") from None


def source_of(url: str, link: dict[str, str], found: dict[str, str]) -> dict[str, Any]:
    return {"kind": "github", "url": url.strip(), "ref": found["ref"], "sha": found["sha"],
            "subdir": link["subdir"], "branch_given": bool(link["ref"])}


def write_folder(root: Path, plugin_id: str, files: dict[str, bytes]) -> Path:
    """Replace data/plugins/<id>/ with `files` so that a failure never leaves half a plugin."""
    root.mkdir(parents=True, exist_ok=True)
    target = root / plugin_id
    staging = root / f".new-{plugin_id}"
    old = root / f".old-{plugin_id}"
    for leftover in (staging, old):
        if leftover.exists():
            shutil.rmtree(leftover)
    for rel, data in files.items():
        path = staging / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if target.exists():
        target.rename(old)
    staging.rename(target)
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    return target
