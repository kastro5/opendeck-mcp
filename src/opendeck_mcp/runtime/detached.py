"""Applying from inside OpenDeck: the plugin build hands apply/restore to a detached helper.

Applying stops OpenDeck and every plugin process, this server included when it
runs as the OpenDeck MCP plugin. So once the applier's checks pass, the plugin
starts a copy of itself (`apply-helper`) outside OpenDeck's process tree, which
does the stop/write/start and leaves its report in `last_apply.json`. The
restarted plugin hands that report to the next tool call.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

from ..core.jsonio import atomic_write
from ..core.paths import Paths, os_name

ENV = "OPENDECK_MCP_PLUGIN"
log = logging.getLogger("opendeck_mcp.detached")


def active() -> bool:
    """Whether this process is the plugin build (set by the plugin entry point)."""
    return os.environ.get(ENV) == "1"


def result_file(paths: Paths) -> Path:
    return paths.data_dir / "last_apply.json"


def _write(paths: Paths, data: dict) -> None:
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(result_file(paths), json.dumps(data, indent=1).encode())


def _read(paths: Paths) -> dict | None:
    try:
        return json.loads(result_file(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def helper_command(job: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "apply-helper", "--job", str(job)]
    return [sys.executable, "-m", "opendeck_mcp", "apply-helper", "--job", str(job)]


def _spawn(command: list[str], log_file: Path) -> None:
    out = open(log_file, "ab")
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": out, "stderr": out, "close_fds": True}
    try:
        if os_name() == "windows":
            detached, new_group, no_window, breakaway = 0x00000008, 0x00000200, 0x08000000, 0x01000000
            try:
                subprocess.Popen(command, creationflags=detached | new_group | no_window | breakaway, **kwargs)
            except OSError:
                subprocess.Popen(command, creationflags=detached | new_group | no_window, **kwargs)
        else:
            subprocess.Popen(command, start_new_session=True, **kwargs)
    finally:
        out.close()


def start(paths: Paths, kind: str, **args) -> str:
    """Record the job and start the helper. kind: 'apply' or 'restore'."""
    job_id = uuid.uuid4().hex[:8]
    job = paths.data_dir / "apply_job.json"
    atomic_write(job, json.dumps({"id": job_id, "kind": kind, "args": args}).encode())
    _write(paths, {"id": job_id, "kind": kind, "status": "running", "started": time.time()})
    _spawn(helper_command(job), paths.data_dir / "apply-helper.log")
    verb = "Applying" if kind == "apply" else "Restoring"
    return (f"{verb} now (job {job_id}). OpenDeck stops and restarts in about 10 seconds, and this server "
            "with it, so this call can't wait for the result: the next opendeck tool call reports it "
            "(get_overview is a good one).")


def run_job(paths: Paths, job: Path) -> int:
    """The helper's body: run the apply/restore it was given and leave the report."""
    from . import applier, process

    spec = json.loads(job.read_text(encoding="utf-8"))
    args = spec["args"]
    try:
        if spec["kind"] == "apply":
            text = applier.apply(paths, True, args.get("show_window", False), args.get("on_conflict", "abort"),
                                 args.get("allow_untested_version", False))
        else:
            text = applier.restore(paths, args["backup_id"], True, args.get("show_window", False))
        status = "done"
    except Exception as exc:  # the report is the only way the error reaches anyone
        log.error("%s", traceback.format_exc())
        text, status = f"Failed: {exc!r}", "failed"
        # With the plugin gone nobody else would bring OpenDeck back.
        try:
            if not process.is_running(paths):
                started = process.start(paths, hidden=not args.get("show_window", False))
                text += "\nOpenDeck was restarted." if started else "\n⚠ OpenDeck is stopped; start it manually."
        except Exception as again:
            text += f"\n⚠ OpenDeck is stopped and couldn't be restarted ({again!r}); start it manually."
    _write(paths, {"id": spec["id"], "kind": spec["kind"], "status": status, "text": text,
                   "finished": time.time()})
    job.unlink(missing_ok=True)
    return 0 if status == "done" else 1


def take_report(paths: Paths) -> str | None:
    """The helper's report, once; or a note while it's still running."""
    data = _read(paths)
    if not data or data.get("seen"):
        return None
    what = "apply_changes" if data.get("kind") == "apply" else "restore_backup"
    if data.get("status") == "running":
        if time.time() - data.get("started", 0) < 120:
            return f"Note: {what} (job {data.get('id')}) is still running; OpenDeck may be restarting."
        return f"⚠ {what} (job {data.get('id')}) never reported back; check apply-helper.log in {paths.data_dir}."
    data["seen"] = True
    _write(paths, data)
    return f"Result of the last {what} (job {data.get('id')}):\n{data.get('text', '')}"
