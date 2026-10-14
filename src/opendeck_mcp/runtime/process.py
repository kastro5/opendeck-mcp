"""Finding, flushing, stopping and starting the OpenDeck process."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import psutil

from ..core.paths import Paths, os_name


def _norm(path: str | Path | None) -> str:
    return os.path.normcase(os.path.realpath(str(path))) if path else ""


def plugin_roots(paths: Paths) -> list[str]:
    roots = [_norm(paths.plugins_dir)]
    if paths.resource_plugins_dir:
        roots.append(_norm(paths.resource_plugins_dir))
    return roots


def find(paths: Paths) -> list[psutil.Process]:
    """OpenDeck itself plus any plugin process still running from its plugin folders."""
    exe = _norm(paths.exe)
    roots = plugin_roots(paths)
    found = []
    for proc in psutil.process_iter(["exe", "name"]):
        try:
            path = _norm(proc.info["exe"])
        except (psutil.Error, OSError):
            continue
        if not path:
            continue
        if (exe and path == exe) or any(path.startswith(root + os.sep) for root in roots):
            found.append(proc)
    return found


def is_running(paths: Paths) -> bool:
    exe = _norm(paths.exe)
    return any(_norm(p.info["exe"]) == exe for p in find(paths))


def send_message(paths: Paths, message: dict) -> None:
    """Hand an event to the running OpenDeck (its single-instance CLI forwards --process-message)."""
    if paths.exe is None:
        raise RuntimeError("OpenDeck's executable wasn't found; set OPENDECK_EXE")
    # An argument list, never a shell string: Windows PowerShell strips the JSON's quotes.
    subprocess.run([str(paths.exe), "--process-message", json.dumps(message)], timeout=30,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=subprocess.CREATE_NO_WINDOW if os_name() == "windows" else 0)


def flush(paths: Paths, selections: dict[str, str], timeout: float = 4.0) -> dict[str, bool]:
    """Make OpenDeck write each device's selected profile now (re-selecting it saves it first).

    Returns device -> whether the file was seen to change. Devices that aren't
    connected don't get saved, which is fine: only connected devices have
    unsaved plugin-driven changes.
    """
    from ..core.profile import profile_path

    stamps = {}
    for device, profile in selections.items():
        path = profile_path(paths, device, profile)
        stamps[device] = (path, path.stat().st_mtime_ns if path.exists() else None)
        send_message(paths, {"event": "switchProfile", "device": device, "profile": profile})
    deadline = time.monotonic() + timeout
    result = {device: False for device in selections}
    while time.monotonic() < deadline and not all(result.values()):
        for device, (path, before) in stamps.items():
            if path.exists() and path.stat().st_mtime_ns != before:
                result[device] = True
        time.sleep(0.1)
    return result


def stop(paths: Paths, timeout: float = 10.0) -> None:
    """Kill OpenDeck and its plugins (the tree), then make sure nothing from it is left."""
    exe = _norm(paths.exe)
    tree: list[psutil.Process] = []
    for proc in find(paths):
        if _norm(proc.info["exe"]) == exe:
            try:
                tree += proc.children(recursive=True)
            except psutil.Error:
                pass
        tree.append(proc)
    for proc in tree:
        try:
            proc.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(tree, timeout=5)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        left = find(paths)
        if not left:
            return
        for proc in left:
            try:
                proc.kill()
            except psutil.Error:
                pass
        time.sleep(0.2)
    names = ", ".join(f"{p.pid}:{p.info.get('name')}" for p in find(paths))
    raise RuntimeError(f"couldn't stop OpenDeck; still running: {names}")


def start(paths: Paths, hidden: bool = True, wait: float = 15.0) -> bool:
    """Start OpenDeck detached from this process (and from any job object), so it outlives us."""
    if paths.exe is None:
        raise RuntimeError("OpenDeck's executable wasn't found; set OPENDECK_EXE")
    args = [str(paths.exe)] + (["--hide"] if hidden else [])
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "close_fds": True, "cwd": str(paths.exe.parent)}
    if os_name() == "windows":
        detached, new_group, breakaway = 0x00000008, 0x00000200, 0x01000000
        try:
            subprocess.Popen(args, creationflags=detached | new_group | breakaway, **kwargs)
        except OSError:
            subprocess.Popen(args, creationflags=detached | new_group, **kwargs)
    else:
        subprocess.Popen(args, start_new_session=True, **kwargs)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if is_running(paths):
            return True
        time.sleep(0.25)
    return False


def version(paths: Paths) -> str | None:
    try:
        from ..core.jsonio import read_json

        return str(read_json(paths.app_settings_file).get("version"))
    except (OSError, ValueError, AttributeError):
        return None
