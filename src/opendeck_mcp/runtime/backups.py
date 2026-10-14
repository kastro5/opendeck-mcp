"""Backups taken before every apply/restore, and restoring them.

A backup holds the whole profiles/ tree (small), applications.json, and every
other path the change touches (plugin settings, image folders, plugin
folders). Each entry records whether it existed, so restoring also deletes
things the change created.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from ..core.paths import Paths

KEEP_COUNT = 30
KEEP_BYTES = 500 * 1024 * 1024
ALWAYS_KEEP = 5


@dataclass
class Backup:
    id: str
    path: Path
    meta: dict

    @property
    def summary(self) -> str:
        what = "; ".join(self.meta.get("changes") or []) or self.meta.get("label", "")
        return f"{self.id} · {self.meta.get('created', '?')} · {self.meta.get('kind', '?')} · {what}"


def root(paths: Paths) -> Path:
    return paths.data_dir / "backups"


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst)


def create(paths: Paths, rels: set[str], kind: str, changes: list[str], plan: list[dict] | None = None,
           version: str | None = None) -> Backup:
    """Snapshot the config paths in `rels` (config-relative, '/'-separated) plus profiles/, images/
    (OpenDeck writes key images there after loading data: URLs) and applications.json."""
    rels = set(rels) | {"profiles", "images", "applications.json"}
    # Drop entries already covered by a parent entry (e.g. profiles/x inside profiles).
    rels = {r for r in rels if not any(r != p and r.startswith(p + "/") for p in rels)}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_id = stamp
    n = 1
    while (root(paths) / backup_id).exists():
        n += 1
        backup_id = f"{stamp}-{n}"
    target = root(paths) / backup_id
    entries = []
    for rel in sorted(rels):
        src = paths.config_dir / rel
        existed = src.exists()
        entries.append({"rel": rel, "existed": existed, "kind": "dir" if src.is_dir() else "file"})
        if existed:
            _copy(src, target / "files" / rel)
    meta = {"id": backup_id, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "changes": changes,
            "opendeck_version": version, "entries": entries}
    target.mkdir(parents=True, exist_ok=True)
    (target / "backup.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    if plan is not None:
        (target / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    prune(paths)
    return Backup(backup_id, target, meta)


def restore_files(paths: Paths, backup: Backup) -> None:
    """Put every entry back the way it was (OpenDeck must not be running)."""
    for entry in backup.meta["entries"]:
        dest = paths.config_dir / entry["rel"]
        if dest.is_dir():
            shutil.rmtree(dest)
        elif dest.exists():
            dest.unlink()
        if entry["existed"]:
            _copy(backup.path / "files" / entry["rel"], dest)


def list_all(paths: Paths) -> list[Backup]:
    out = []
    if root(paths).is_dir():
        for folder in root(paths).iterdir():
            try:
                meta = json.loads((folder / "backup.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append(Backup(folder.name, folder, meta))
    return sorted(out, key=lambda b: b.id, reverse=True)


def get(paths: Paths, backup_id: str) -> Backup:
    for backup in list_all(paths):
        if backup.id == backup_id:
            return backup
    raise ValueError(f"no backup {backup_id!r}; see list_backups")


def _size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def prune(paths: Paths) -> None:
    backups = list_all(paths)
    total = 0
    for index, backup in enumerate(backups):
        total += _size(backup.path)
        if index >= ALWAYS_KEEP and (index >= KEEP_COUNT or total > KEEP_BYTES):
            shutil.rmtree(backup.path, ignore_errors=True)
