"""Instant edits: change an existing key's title, image or settings in the running OpenDeck.

Titles and images go through `--process-message` (setTitle / setImage), which
OpenDeck runs without plugin auth. Settings can't: a setSettings arriving that
way is only echoed to the property inspector, so a plugin that caches its
settings would never hear about it. Instead we connect to OpenDeck's plugin
websocket as the key's property inspector and send setSettings from there,
which is exactly what OpenDeck's own settings panel does, so the plugin gets
didReceiveSettings.

OpenDeck only marks the device's *selected* profile as unsaved, so live edits
are limited to that profile; a flush afterwards writes it out at once.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path
from typing import Any

import psutil

from ..core.jsonio import read_json
from ..core.paths import Paths
from . import process

DEFAULT_PORT = 57116
JOURNAL_KEEP = 200


class LiveError(RuntimeError):
    pass


def listening_port(paths: Paths) -> int:
    """OpenDeck's plugin websocket port: the lower of the pair it binds (base and base + 2)."""
    exe = process._norm(paths.exe)
    for proc in process.find(paths):
        if process._norm(proc.info["exe"]) != exe:
            continue
        try:
            ports = {c.laddr.port for c in proc.net_connections("tcp")
                     if c.status == psutil.CONN_LISTEN and c.laddr.ip in ("127.0.0.1", "::1")}
        except psutil.Error:
            continue
        pairs = sorted(p for p in ports if p + 2 in ports)
        if pairs:
            return pairs[0]
    return DEFAULT_PORT


def context_string(device: str, profile: str, instance: dict) -> str:
    """The full context OpenDeck uses in events: <device>.<profile>.<Controller>.<pos>.<index>."""
    from ..core.profile import Ctx

    return f"{device}.{profile}.{Ctx.parse(instance['context'])}"


# -- sending ------------------------------------------------------------------------- #

def send_title(paths: Paths, context: str, state: int, text: str) -> None:
    process.send_message(paths, {"event": "setTitle", "context": context,
                                 "payload": {"title": text, "state": state}})


def send_image(paths: Paths, context: str, state: int, image: str | None) -> None:
    """A data: URL, or None for the action's own image."""
    payload: dict[str, Any] = {"state": state}
    if image is not None:
        payload["image"] = image
    process.send_message(paths, {"event": "setImage", "context": context, "payload": payload})


def send_settings(paths: Paths, context: str, settings: dict, port: int | None = None) -> None:
    from websockets.sync.client import connect

    url = f"ws://127.0.0.1:{port or listening_port(paths)}"
    try:
        with connect(url, open_timeout=5, close_timeout=2) as ws:
            ws.send(json.dumps({"event": "registerPropertyInspector", "uuid": context}))
            ws.send(json.dumps({"event": "setSettings", "context": context, "payload": settings}))
            time.sleep(0.2)  # let OpenDeck read both frames before the socket closes
    except OSError as exc:
        raise LiveError(f"couldn't reach OpenDeck's websocket at {url}: {exc}") from exc


# -- images -------------------------------------------------------------------------- #

def image_as_data_url(paths: Paths, device: str, profile: str, instance: dict, state: int) -> str | None:
    """The image a state shows now, as something setImage can take back: a data: URL, or None
    when it is the action's own image (setImage without an image restores that)."""
    value = instance["states"][state].get("image") or ""
    defaults = instance["action"]["states"]
    if not value or value == defaults[min(state, len(defaults) - 1)].get("image"):
        return None
    if value.startswith("data:"):
        return value
    for candidate in (paths.images_dir / device / profile / instance["context"] / value,
                      paths.config_dir / value, Path(value)):
        try:
            if candidate.is_file():
                mime = mimetypes.guess_type(candidate.name)[0] or "image/png"
                return f"data:{mime};base64," + base64.b64encode(candidate.read_bytes()).decode()
        except OSError:
            continue
    return None


# -- journal ------------------------------------------------------------------------- #

def journal_file(paths: Paths) -> Path:
    return paths.data_dir / "live_journal.jsonl"


def read_journal(paths: Paths) -> list[dict]:
    try:
        lines = journal_file(paths).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def write_journal(paths: Paths, entries: list[dict]) -> None:
    from ..core.jsonio import atomic_write

    entries = entries[-JOURNAL_KEEP:]
    body = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
    atomic_write(journal_file(paths), body.encode("utf-8"))


# -- applying ------------------------------------------------------------------------ #

def snapshot(paths: Paths, device: str, profile: str, instance: dict, states: list[int]) -> dict:
    """What undo needs to put a key back: titles, images (as bytes) and settings."""
    return {
        "titles": {str(i): instance["states"][i].get("text", "") for i in states},
        "images": {str(i): image_as_data_url(paths, device, profile, instance, i) for i in states},
        "settings": instance.get("settings"),
    }


def push(paths: Paths, context: str, titles: dict[int, str], images: dict[int, str | None],
         settings: dict | None) -> None:
    for state, text in titles.items():
        send_title(paths, context, state, text)
    for state, image in images.items():
        send_image(paths, context, state, image)
    if settings is not None:
        send_settings(paths, context, settings)


def read_instance(paths: Paths, device: str, profile: str, controller: str, position: int,
                  child: int | None) -> dict | None:
    from ..core.profile import ARRAY_FOR, profile_path

    doc = read_json(profile_path(paths, device, profile))
    slots = doc.get(ARRAY_FOR[controller]) or []
    inst = slots[position] if position < len(slots) else None
    if inst is not None and child:
        kids = inst.get("children") or []
        inst = kids[child - 1] if child <= len(kids) else None
    return inst
