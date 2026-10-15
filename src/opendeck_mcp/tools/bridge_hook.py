"""Stage the companion bridge plugin alongside the first real change, if it's missing or old."""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from .. import __version__
from ..core.devices import BRIDGE_ID, plugin_installed
from ..core.jsonio import read_json
from ..core.paths import Paths


def installed_version(paths: Paths) -> str | None:
    try:
        return str(read_json(paths.plugins_dir / BRIDGE_ID / "manifest.json").get("Version"))
    except (OSError, ValueError, AttributeError):
        return None


def prepare_source(paths: Paths) -> Path:
    """Copy the packaged bridge into the cache with its version filled in."""
    target = paths.data_dir / "cache" / "bridge" / __version__ / BRIDGE_ID
    if (target / "manifest.json").exists():
        return target
    source = resources.files("opendeck_mcp") / "bridge_plugin" / BRIDGE_ID
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        data = item.read_bytes()
        if item.name in ("manifest.json", "bridge.js"):
            data = data.replace(b"__BRIDGE_VERSION__", __version__.encode())
        (target / item.name).write_bytes(data)
    return target


def bridge_records(paths: Paths, records: list[dict], current: dict) -> list[dict]:
    if installed_version(paths) == __version__ or plugin_installed(paths):
        return []
    if any(r["op"] == "install_bridge" for r in records):
        return []
    source = prepare_source(paths)
    return [{"id": f"op{current['next_id']}", "op": "install_bridge", "pre": {},
             "args": {"folder": BRIDGE_ID, "source": str(source), "version": __version__}}]


def remove_cached(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
