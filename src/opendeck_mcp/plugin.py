"""opendeck-mcp as an OpenDeck plugin: OpenDeck starts it, and it serves MCP over HTTP.

OpenDeck runs a native plugin as `<exe> -port P -pluginUUID U -registerEvent E -info JSON`
and sends its output to the plugin log. This process:
- registers with OpenDeck and records device geometry in its global settings, in the
  bridge plugin's format (so the bridge isn't needed alongside it);
- serves the MCP server on the address kept in the same settings (127.0.0.1:8765 unless
  changed), with the bearer token shared with `opendeck-mcp serve`;
- answers the Plugins tab's gear button with a settings page in the browser.

It writes how the listener is doing to `plugin_status.json`, which get_overview reports.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from . import http as H
from .core.jsonio import atomic_write
from .core.paths import Paths, discover
from .runtime import detached

log = logging.getLogger("opendeck_mcp.plugin")
DEFAULT_SERVER = {"host": "127.0.0.1", "port": H.DEFAULT_PORT, "allowed_hosts": []}


def status_file(paths: Paths) -> Any:
    return paths.data_dir / "plugin_status.json"


def read_status(paths: Paths) -> dict | None:
    try:
        return json.loads(status_file(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def describe_status(paths: Paths) -> str | None:
    """One line for get_overview, or None when the plugin has never run."""
    import psutil

    status = read_status(paths)
    if not status:
        return None
    if not psutil.pid_exists(status.get("pid", -1)):
        return "OpenDeck MCP plugin: not running"
    if status.get("state") == "listening":
        return f"OpenDeck MCP plugin: serving on {status['url']}"
    return f"OpenDeck MCP plugin: ⚠ not serving: {status.get('error')}"


def check_server(server: dict) -> dict:
    """Validate a server config from settings or the settings page; raise ValueError."""
    host = str(server.get("host") or "127.0.0.1").strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(f"the address must be an IP address, not {host!r}") from None
    if address.is_unspecified:
        raise ValueError("listening on every network (0.0.0.0) isn't offered here; pick one address, "
                         "such as this PC's Tailscale IP")
    try:
        port = int(server.get("port") or H.DEFAULT_PORT)
    except (TypeError, ValueError):
        raise ValueError("the port must be a number") from None
    if not 1024 <= port <= 65535:
        raise ValueError("the port must be between 1024 and 65535")
    extra = server.get("allowed_hosts") or []
    if isinstance(extra, str):
        extra = extra.split(",")
    extra = [h.strip() for h in extra if h and h.strip()]
    return {"host": host, "port": port, "allowed_hosts": extra}


def local_addresses() -> list[tuple[str, str]]:
    """(address, interface) for this machine's IPv4 addresses, loopback first."""
    import psutil

    found = [("127.0.0.1", "this computer only")]
    for name, addresses in psutil.net_if_addrs().items():
        for a in addresses:
            if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                found.append((a.address, name))
    return found


class Listener:
    """The MCP HTTP server, run by uvicorn in a thread so it can be restarted with new settings."""

    def __init__(self, paths: Paths):
        self.paths = paths
        self.server = None
        self.thread: threading.Thread | None = None
        self.config: dict = dict(DEFAULT_SERVER)
        self.state: dict = {"state": "starting"}

    @property
    def url(self) -> str:
        host = self.config["host"]
        return f"http://{f'[{host}]' if ':' in host else host}:{self.config['port']}/mcp"

    def _report(self, state: str, error: str | None = None) -> None:
        self.state = {"state": state, "url": self.url, "host": self.config["host"], "port": self.config["port"],
                      "error": error, "pid": os.getpid(), "version": __version__, "updated": time.time()}
        atomic_write(status_file(self.paths), json.dumps(self.state, indent=1).encode())
        (log.info if state == "listening" else log.error)("%s %s %s", state, self.url, error or "")

    def start(self, config: dict) -> None:
        import uvicorn

        self.stop()
        self.config = config
        family = socket.AF_INET6 if ":" in config["host"] else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.bind((config["host"], config["port"]))
            sock.listen(128)
        except OSError as exc:
            sock.close()
            reason = ("the port is already in use (is `opendeck-mcp serve` running on it?)"
                      if exc.errno in (98, 48, 10048) else
                      "that address isn't on this computer" if exc.errno in (99, 49, 10049) else str(exc))
            self._report("error", f"can't listen on {config['host']}:{config['port']}: {reason}")
            return
        app = H.build_app(config["host"], H.get_token(self.paths), config["allowed_hosts"], stateless=True)
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [sock]}, daemon=True,
                                       name="mcp-http")
        self.thread.start()
        self._report("listening")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
            if self.thread is not None:
                self.thread.join(timeout=5)
        self.server = self.thread = None


def _device(info: dict) -> dict:
    size = info.get("size") or {}
    return {"name": info.get("name"), "rows": size.get("rows"), "columns": size.get("columns"),
            "type": info.get("type"), "connected": True,
            "last_seen": datetime.now(UTC).isoformat(timespec="seconds")}


class Plugin:
    def __init__(self, paths: Paths, uuid: str, info: dict):
        self.paths = paths
        self.uuid = uuid
        self.info = info
        self.settings: dict | None = None  # None until OpenDeck has sent the stored ones
        self.sent = ""
        self.listener = Listener(paths)
        self.page = None
        self.ws = None
        self.loop: asyncio.AbstractEventLoop | None = None

    async def save(self) -> None:
        body = json.dumps(self.settings, sort_keys=True)
        # OpenDeck echoes setGlobalSettings back as didReceiveGlobalSettings; sending only real
        # changes keeps that from looping.
        if body == self.sent:
            return
        self.sent = body
        await self.ws.send(json.dumps({"event": "setGlobalSettings", "context": self.uuid,
                                       "payload": self.settings}))

    def server_config(self) -> dict:
        try:
            return check_server({**DEFAULT_SERVER, **(self.settings.get("server") or {})})
        except ValueError as exc:
            log.error("stored server settings are invalid (%s); using the defaults", exc)
            return dict(DEFAULT_SERVER)

    async def loaded(self, stored: dict) -> None:
        devices = {d: {**v, "connected": False} for d, v in (stored.get("devices") or {}).items()}
        for device in self.info.get("devices") or []:
            devices[device["id"]] = _device(device)
        self.settings = {"schema": 1, "bridge_version": __version__, "devices": devices,
                         "server": stored.get("server") or dict(DEFAULT_SERVER)}
        await self.save()
        await asyncio.to_thread(self.listener.start, self.server_config())

    async def handle(self, message: dict) -> None:
        event = message.get("event")
        log.info("event %s", event)
        try:
            await self._handle(event, message)
        except Exception:  # one bad event mustn't take the server down with it
            log.exception("handling %s failed", event)

    async def _handle(self, event: str | None, message: dict) -> None:
        if event == "deviceDidConnect":
            self.settings["devices"][message["device"]] = _device(message.get("deviceInfo") or {})
            await self.save()
        elif event == "deviceDidDisconnect":
            self.settings["devices"].setdefault(message["device"], {})["connected"] = False
            await self.save()
        elif event == "showSettingsInterface":
            await asyncio.to_thread(self.open_page)

    # -- settings page callbacks (called from the page's thread) -- #

    def open_page(self) -> None:
        from .settings_page import SettingsPage

        if self.page is not None:
            self.page.close()
        self.page = SettingsPage(self)
        self.page.open()

    def change_server(self, server: dict) -> None:
        config = check_server(server)
        previous = self.listener.config
        self.listener.start(config)
        if self.listener.state.get("state") != "listening":
            error = self.listener.state.get("error")
            self.listener.start(previous)  # keep serving where clients already point
            raise ValueError(error)
        self.settings["server"] = config
        asyncio.run_coroutine_threadsafe(self.save(), self.loop).result(timeout=5)

    def reset_token(self) -> None:
        H.get_token(self.paths, reset=True)
        self.listener.start(self.listener.config)

    async def run(self, port: int, register_event: str) -> None:
        import websockets

        self.loop = asyncio.get_running_loop()
        async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=None) as ws:
            self.ws = ws
            await ws.send(json.dumps({"event": register_event, "uuid": self.uuid}))
            await ws.send(json.dumps({"event": "getGlobalSettings", "context": self.uuid}))
            pending: list[dict] = []
            async for raw in ws:
                message = json.loads(raw)
                if self.settings is None:
                    if message.get("event") != "didReceiveGlobalSettings":
                        pending.append(message)
                        continue
                    await self.loaded((message.get("payload") or {}).get("settings") or {})
                    for early in pending:
                        await self.handle(early)
                    continue
                await self.handle(message)
        log.info("OpenDeck closed the connection; exiting")


def locate_opendeck(executable: str | None = None, parent_exe: str | None = None) -> None:
    """Point discovery at the OpenDeck that started us rather than at default install paths.

    The config folder is where the plugin sits (<config>/plugins/<id>.sdPlugin/bin/<os>/<exe>), and
    the executable is our parent process. Both go into the environment, so the apply helper inherits
    them. Explicit OPENDECK_* settings win.
    """
    if "OPENDECK_CONFIG_DIR" not in os.environ and (executable or getattr(sys, "frozen", False)):
        here = Path(executable or sys.executable).resolve()
        if len(here.parents) > 4 and here.parents[3].name == "plugins":
            os.environ["OPENDECK_CONFIG_DIR"] = str(here.parents[4])
    if "OPENDECK_EXE" not in os.environ:
        if parent_exe is None:
            import psutil

            try:
                parent_exe = psutil.Process().parent().exe()
            except (psutil.Error, AttributeError, OSError):
                parent_exe = None
        if parent_exe and Path(parent_exe).stem.lower() == "opendeck":
            os.environ["OPENDECK_EXE"] = parent_exe


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="opendeck-mcp (OpenDeck plugin)")
    parser.add_argument("-port", type=int, required=True)
    parser.add_argument("-pluginUUID", required=True)
    parser.add_argument("-registerEvent", default="registerPlugin")
    parser.add_argument("-info", default="{}")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    os.environ[detached.ENV] = "1"
    try:
        info = json.loads(args.info)
    except ValueError:
        info = {}
    locate_opendeck()
    paths = discover()
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    plugin = Plugin(paths, args.pluginUUID, info)
    log.info("opendeck-mcp %s starting as plugin %s (config %s, exe %s)", __version__, args.pluginUUID,
             paths.config_dir, paths.exe)
    try:
        asyncio.run(plugin.run(args.port, args.registerEvent))
    finally:
        plugin.listener.stop()
        if plugin.page is not None:
            plugin.page.close()
