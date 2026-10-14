"""The staged plan: an ordered list of operations, persisted between tool calls."""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..core.jsonio import atomic_write
from ..core.paths import Paths

SCHEMA = 1


def plan_file(paths: Paths) -> Path:
    return paths.data_dir / "plan.json"


def load(paths: Paths) -> dict[str, Any]:
    try:
        data = json.loads(plan_file(paths).read_text(encoding="utf-8"))
        if data.get("schema") == SCHEMA and isinstance(data.get("ops"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"schema": SCHEMA, "plan_id": uuid.uuid4().hex[:8], "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "next_id": 1, "ops": []}


def save(paths: Paths, plan: dict[str, Any]) -> None:
    atomic_write(plan_file(paths), json.dumps(plan, ensure_ascii=False, indent=1).encode("utf-8"))


def clear(paths: Paths) -> None:
    try:
        plan_file(paths).unlink()
    except FileNotFoundError:
        pass


@contextlib.contextmanager
def locked(paths: Paths, timeout: float = 30.0) -> Iterator[None]:
    """A cross-process lock so two agent sessions can't edit or apply the plan at once."""
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    lock = paths.data_dir / "lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:  # a lock left by a dead process
                pid = int(lock.read_text() or 0)
                import psutil

                if not psutil.pid_exists(pid) or time.time() - lock.stat().st_mtime > 600:
                    lock.unlink(missing_ok=True)
                    continue
            except (OSError, ValueError):
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("another opendeck-mcp session is busy; try again shortly") from None
            time.sleep(0.1)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)
