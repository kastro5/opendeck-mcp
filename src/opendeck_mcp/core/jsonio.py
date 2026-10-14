"""JSON exactly as OpenDeck (serde_json's pretty printer) writes it, and safe writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Never ends in .json/.json.bak/.json.temp: OpenDeck lists those as profiles.
TEMP_SUFFIX = ".mcptmp"


def dumps(obj: Any) -> bytes:
    """Byte-identical to serde_json::to_string_pretty over a sorted map (no trailing newline)."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, separators=(",", ": ")).encode("utf-8")


def dumps_compact(obj: Any) -> bytes:
    """serde_json::to_string over a sorted map: how plugin global settings are stored."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def loads(data: bytes) -> Any:
    return json.loads(data.decode("utf-8-sig"))


def read_json(path: Path) -> Any:
    return loads(path.read_bytes())


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + TEMP_SUFFIX)
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7396 JSON merge patch, as OpenDeck applies manifest.<os>.json."""
    if not isinstance(patch, dict):
        return patch
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result
