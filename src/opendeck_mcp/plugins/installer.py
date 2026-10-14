"""Unpacking a downloaded plugin into the cache, safely, and describing it."""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..core.manifest import read_manifest
from ..core.paths import Paths, os_name

_ID = re.compile(r"^[A-Za-z0-9._-]+\.sdPlugin$")


class InstallError(RuntimeError):
    pass


@dataclass
class Unpacked:
    folder: str  # e.g. com.example.thing.sdPlugin
    path: Path  # cache copy
    sha256: str
    size: int
    manifest: dict


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _open_zip(data: bytes) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in archive.namelist() if not n.endswith("/")]
    # Some releases wrap the plugin zip in another zip.
    if len(names) == 1 and names[0].lower().endswith((".zip", ".streamdeckplugin")):
        return _open_zip(archive.read(names[0]))
    return archive


def unpack(paths: Paths, archive_path: Path, fallback_id: str | None = None) -> Unpacked:
    try:
        archive = _open_zip(archive_path.read_bytes())
    except zipfile.BadZipFile as exc:
        raise InstallError(f"not a plugin archive: {exc}") from exc
    members = archive.infolist()
    folder = None
    for info in members:
        parts = PurePosixPath(info.filename.replace("\\", "/")).parts
        for part in parts:
            if part.endswith(".sdPlugin"):
                folder = part
                break
        if folder:
            break
    if folder is None:
        if not fallback_id:
            raise InstallError("the archive contains no *.sdPlugin folder")
        folder = fallback_id if fallback_id.endswith(".sdPlugin") else fallback_id + ".sdPlugin"
    if not _ID.match(folder):
        raise InstallError(f"unexpected plugin folder name {folder!r}")
    target = paths.data_dir / "cache" / "pending" / folder
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for info in members:
        name = info.filename.replace("\\", "/")
        if info.is_dir():
            continue
        parts = PurePosixPath(name).parts
        if name.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
            raise InstallError(f"unsafe path in archive: {info.filename}")
        if folder in parts:
            rel = PurePosixPath(*parts[parts.index(folder) + 1:])
        elif fallback_id:
            rel = PurePosixPath(*parts)
        else:
            continue
        if not rel.parts:
            continue
        dest = target.joinpath(*rel.parts)
        if not dest.resolve().is_relative_to(target.resolve()):
            raise InstallError(f"unsafe path in archive: {info.filename}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
    if not (target / "manifest.json").exists():
        raise InstallError("the plugin has no manifest.json")
    try:
        manifest = read_manifest(target)
    except Exception as exc:
        raise InstallError(f"unreadable manifest.json: {exc}") from exc
    return Unpacked(folder, target, sha256(archive_path), archive_path.stat().st_size, manifest)


def runtime_of(manifest: dict) -> str:
    code = (manifest.get("CodePathWin") if os_name() == "windows" else
            manifest.get("CodePathMac") if os_name() == "mac" else manifest.get("CodePathLin")) \
        or manifest.get("CodePath") or ""
    if (manifest.get("CodePaths") or {}):
        code = code or next(iter(manifest["CodePaths"].values()))
    lowered = code.lower()
    if lowered.endswith((".html", ".htm")):
        return f"web page ({code}), runs in OpenDeck's built-in browser"
    if lowered.endswith((".js", ".mjs", ".cjs")):
        return f"Node.js script ({code}); needs Node.js 20+ installed"
    return f"native program ({code})" if code else "no code path"


def os_support(manifest: dict) -> tuple[bool, str]:
    platforms = [str(o.get("Platform", "")).lower() for o in manifest.get("OS") or [] if isinstance(o, dict)]
    here = os_name()
    if here in platforms:
        return True, ", ".join(platforms)
    if "windows" in platforms and here != "windows":
        return True, ", ".join(platforms) + " (runs under Wine on this OS)"
    return False, ", ".join(platforms) or "none listed"
