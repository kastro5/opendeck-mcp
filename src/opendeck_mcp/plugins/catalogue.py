"""The plugin catalogues OpenDeck's Plugin Manager uses, and resolving a download for a plugin."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..core.paths import Paths

OPENACTION = "https://openactionapi.github.io/plugins/catalogue.json"
ARCHIVE = "https://plugins.amankhanna.me/catalogue.json"
ARCHIVE_DOWNLOAD = "https://plugins.amankhanna.me/rezipped/{id}.zip"
CACHE_SECONDS = 6 * 3600
HEADERS = {"User-Agent": "opendeck-mcp", "Accept": "application/json"}


@dataclass
class Listing:
    id: str
    name: str
    author: str
    description: str
    repository: str | None
    version: str | None
    download_url: str | None
    ai_assisted: bool
    source: str


class CatalogueError(RuntimeError):
    pass


def _client() -> httpx.Client:
    return httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30)


def _cached_json(paths: Paths, name: str, url: str) -> object:
    cache = paths.data_dir / "cache" / f"{name}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_SECONDS:
        return json.loads(cache.read_text(encoding="utf-8"))
    try:
        with _client() as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        raise CatalogueError(f"couldn't fetch {url}: {exc}") from exc
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def openaction(paths: Paths) -> list[Listing]:
    data = _cached_json(paths, "openaction-catalogue", OPENACTION)
    out = []
    for plugin_id, v in (data or {}).items():
        flags = v.get("flags") or {}
        out.append(Listing(plugin_id, v.get("name", plugin_id), v.get("author", "?"), v.get("description", ""),
                           v.get("repository"), None, v.get("download_url"), bool(flags.get("ai_assisted")),
                           "catalogue"))
    return out


def archive(paths: Paths) -> list[Listing]:
    data = _cached_json(paths, "elgato-archive", ARCHIVE)
    return [Listing(v["id"], v.get("name", v["id"]), v.get("author", "?"), "", v.get("link"), v.get("version"),
                    ARCHIVE_DOWNLOAD.format(id=v["id"]), False, "archive") for v in data or []]


def search(listings: list[Listing], query: str | None) -> list[Listing]:
    if not query:
        return listings
    q = query.lower()
    return [item for item in listings
            if q in f"{item.id} {item.name} {item.author} {item.description}".lower()]


_GITHUB = re.compile(r"https://github\.com/([^/]+)/([^/#?]+)")


def resolve_download(listing: Listing) -> tuple[str, str | None]:
    """(download url, version) for a listing; GitHub releases when there's no fixed url."""
    if listing.download_url:
        return listing.download_url, listing.version
    m = _GITHUB.match(listing.repository or "")
    if not m:
        raise CatalogueError(f"{listing.id} has no download url and no GitHub repository")
    owner, repo = m.group(1), m.group(2).removesuffix(".git")
    try:
        with _client() as client:
            response = client.get(f"https://api.github.com/repos/{owner}/{repo}/releases/latest")
            response.raise_for_status()
            release = response.json()
    except httpx.HTTPError as exc:
        raise CatalogueError(f"couldn't read {owner}/{repo}'s latest release: {exc}") from exc
    assets = release.get("assets") or []
    preferred = [a for a in assets if a["name"].lower().endswith(".streamdeckplugin")] or \
                [a for a in assets if a["name"].lower().endswith(".zip")]
    if not preferred:
        raise CatalogueError(f"{owner}/{repo}'s latest release has no .streamDeckPlugin or .zip asset")
    return preferred[0]["browser_download_url"], release.get("tag_name")


def download(paths: Paths, url: str) -> Path:
    target = paths.data_dir / "cache" / "downloads" / re.sub(r"[^\w.-]", "_", url.rsplit("/", 1)[-1] or "plugin")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _client() as client, client.stream("GET", url) as response:
            response.raise_for_status()
            size = 0
            with open(target, "wb") as handle:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 300 * 1024 * 1024:
                        raise CatalogueError("download is larger than 300 MB; refusing")
                    handle.write(chunk)
    except httpx.HTTPError as exc:
        raise CatalogueError(f"download failed: {exc}") from exc
    return target
