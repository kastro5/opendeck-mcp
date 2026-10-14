"""Where OpenDeck keeps things on each platform, with environment overrides."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import platformdirs


def os_name() -> str:
    """The OS as plugin manifests spell it in "OS": windows, mac or linux."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def rust_os() -> str:
    """The OS as OpenDeck names platform manifest overrides: manifest.<rust_os>.json."""
    return {"windows": "windows", "mac": "macos", "linux": "linux"}[os_name()]


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    log_dir: Path
    exe: Path | None
    data_dir: Path  # opendeck-mcp's own state: plan, backups, cache

    @property
    def profiles_dir(self) -> Path:
        return self.config_dir / "profiles"

    @property
    def plugins_dir(self) -> Path:
        return self.config_dir / "plugins"

    @property
    def settings_dir(self) -> Path:
        return self.config_dir / "settings"

    @property
    def images_dir(self) -> Path:
        return self.config_dir / "images"

    @property
    def applications_file(self) -> Path:
        return self.config_dir / "applications.json"

    @property
    def app_settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def resource_plugins_dir(self) -> Path | None:
        """Plugins bundled with the app (re-copied into config on start; never removable)."""
        if self.exe is None:
            return None
        candidate = self.exe.parent / "plugins"
        return candidate if candidate.is_dir() else None

    def rel(self, path: Path) -> str:
        """A config-relative path with forward slashes, as OpenDeck stores them."""
        return path.relative_to(self.config_dir).as_posix()


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def _default_config_dir() -> Path:
    name = os_name()
    if name == "windows":
        return Path(os.environ["APPDATA"]) / "opendeck"
    if name == "mac":
        return Path.home() / "Library" / "Application Support" / "opendeck"
    flatpak = Path.home() / ".var" / "app" / "me.amankhanna.opendeck" / "config" / "opendeck"
    native = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opendeck"
    return flatpak if flatpak.is_dir() and not native.is_dir() else native


def _default_log_dir(config_dir: Path) -> Path:
    name = os_name()
    if name == "windows":
        return Path(os.environ["LOCALAPPDATA"]) / "opendeck" / "logs"
    if name == "mac":
        return Path.home() / "Library" / "Logs" / "opendeck"
    if ".var" in config_dir.parts:
        return Path.home() / ".var" / "app" / "me.amankhanna.opendeck" / "data" / "opendeck" / "logs"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "opendeck" / "logs"


def _default_exe() -> Path | None:
    name = os_name()
    if name == "windows":
        candidate = Path(os.environ["LOCALAPPDATA"]) / "OpenDeck" / "opendeck.exe"
    elif name == "mac":
        candidate = Path("/Applications/OpenDeck.app/Contents/MacOS/opendeck")
    else:
        found = shutil.which("opendeck")
        candidate = Path(found) if found else Path("/usr/bin/opendeck")
    return candidate if candidate.exists() else None


def discover() -> Paths:
    config_dir = _env_path("OPENDECK_CONFIG_DIR") or _default_config_dir()
    return Paths(
        config_dir=config_dir,
        log_dir=_env_path("OPENDECK_LOG_DIR") or _default_log_dir(config_dir),
        exe=_env_path("OPENDECK_EXE") or _default_exe(),
        data_dir=_env_path("OPENDECK_MCP_DATA_DIR")
        or Path(platformdirs.user_data_dir("opendeck-mcp", appauthor=False)),
    )
