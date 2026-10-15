"""The names OpenDeck's app rules match, for apps running now.

OpenDeck (through active-win-pos-rs) names the focused app by its executable's
FileDescription on Windows, falling back to the file name without extension;
on macOS and Linux it is the process/application name.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

import psutil

from .paths import os_name

# Background helpers that never own a window worth a rule.
SKIP = {"svchost", "conhost", "dllhost", "runtimebroker", "searchhost", "backgroundtaskhost", "ctfmon",
        "taskhostw", "sihost", "fontdrvhost", "dwm", "csrss", "wininit", "winlogon", "lsass", "services",
        "smss", "system", "registry", "audiodg", "wmiprvse", "spoolsv", "msedgewebview2", "crashpad_handler"}


def _file_description(path: str) -> str | None:
    version = ctypes.windll.version  # type: ignore[attr-defined]
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, buffer):
        return None
    pointer, length = ctypes.c_void_p(), ctypes.c_uint()
    if not version.VerQueryValueW(buffer, "\\VarFileInfo\\Translation", ctypes.byref(pointer), ctypes.byref(length)) \
            or length.value < 4:
        return None
    lang, codepage = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ushort * 2)).contents
    key = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription"
    if not version.VerQueryValueW(buffer, key, ctypes.byref(pointer), ctypes.byref(length)) or not length.value:
        return None
    return ctypes.wstring_at(pointer, length.value).rstrip("\x00").strip() or None


def app_name(exe: str) -> str:
    if os_name() == "windows":
        try:
            description = _file_description(exe)
        except (OSError, AttributeError):
            description = None
        if description:
            return description
    return Path(exe).stem


def _windowed_pids() -> set[int] | None:
    """PIDs owning a visible, titled top-level window (Windows only; None elsewhere)."""
    if os_name() != "windows":
        return None
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    pids: set[int] = set()
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def collect(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)
        return True

    try:
        user32.EnumWindows(callback_type(collect), 0)
    except OSError:
        return None
    return pids


def running_app_names(user_only: bool = True) -> list[str]:
    """Names of apps the current user has windows open for, as OpenDeck would match them."""
    windowed = _windowed_pids()
    me = None
    if user_only:
        try:
            me = psutil.Process(os.getpid()).username()
        except psutil.Error:
            pass
    names: set[str] = set()
    seen: set[str] = set()
    for proc in psutil.process_iter(["exe", "name", "username"]):
        exe = proc.info.get("exe")
        if not exe or exe in seen or (me and proc.info.get("username") != me):
            continue
        if windowed is not None and proc.pid not in windowed:
            continue
        seen.add(exe)
        if Path(exe).stem.lower() in SKIP or "\\windows\\system32\\" in exe.lower():
            continue
        name = app_name(exe)
        if name and not name.lower().startswith("opendeck"):
            names.add(name)
    return sorted(names, key=str.lower)
