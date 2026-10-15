"""Serving the MCP server over HTTP, for clients on other machines (e.g. over Tailscale).

The server has to run inside the desktop session of the user running OpenDeck:
it stops and restarts OpenDeck and talks to it through its command line, and
neither works from a service or an SSH session. `install_logon_task` sets that
up on Windows.

Every request must carry `Authorization: Bearer <token>`. The token is
generated once and kept in the server's data folder.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from .core.paths import Paths, os_name

DEFAULT_PORT = 8765
TASK_NAME = "opendeck-mcp"
log = logging.getLogger("opendeck_mcp.http")


# -- token --------------------------------------------------------------------------- #

def token_file(paths: Paths) -> Path:
    return paths.data_dir / "http_token"


def get_token(paths: Paths, reset: bool = False) -> str:
    env = os.environ.get("OPENDECK_MCP_TOKEN")
    if env and not reset:
        return env
    file = token_file(paths)
    if file.is_file() and not reset:
        token = file.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    token = secrets.token_urlsafe(32)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(token, encoding="utf-8")
    return token


class BearerAuth:
    """ASGI middleware: reject any HTTP request without the right bearer token (constant-time compare)."""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        given = dict(scope.get("headers") or []).get(b"authorization", b"")
        if not hmac.compare_digest(given, self.expected):
            client = (scope.get("client") or ("?",))[0]
            log.warning("rejected request without a valid token from %s", client)
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": b"missing or wrong bearer token\n"})
            return
        await self.app(scope, receive, send)


# -- serving ------------------------------------------------------------------------- #

def check_bind(host: str, allow_all: bool) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host != "localhost":
            raise SystemExit(f"--host must be an IP address (or localhost), not {host!r}") from None
        return
    if address.is_unspecified and not allow_all:
        raise SystemExit(f"refusing to listen on {host} (every network interface). Bind to one address, "
                         "e.g. this PC's Tailscale IP, or pass --allow-all-interfaces if you really mean it.")


def build_app(host: str, token: str, extra_hosts: list[str], stateless: bool = False):
    """stateless: no MCP session ids, so clients carry on when the server restarts (the plugin build
    restarts with OpenDeck on every apply)."""
    from mcp.server.transport_security import TransportSecuritySettings

    from .server import mcp

    allowed = {f"{host}:*", "127.0.0.1:*", "localhost:*", "[::1]:*", *(f"{h}:*" for h in extra_hosts)}
    if ":" in host and not host.startswith("["):
        allowed.add(f"[{host}]:*")
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=sorted(allowed))
    app = mcp.streamable_http_app(transport_security=security, host=host, stateless_http=stateless)
    return BearerAuth(app, token)


def serve(paths: Paths, host: str, port: int, allow_all: bool, extra_hosts: list[str]) -> None:
    import uvicorn

    check_bind(host, allow_all)
    token = get_token(paths)
    app = build_app(host, token, extra_hosts)
    log_file = paths.data_dir / "http.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    log.warning("serving on http://%s:%d/mcp", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


def client_command(host: str, port: int, token: str) -> str:
    shown = f"[{host}]" if ":" in host else host
    return (f'claude mcp add --transport http opendeck http://{shown}:{port}/mcp '
            f'--header "Authorization: Bearer {token}"')


# -- Windows autostart --------------------------------------------------------------- #
# A script in the user's Startup folder: it runs at logon inside the desktop session, needs no admin
# rights (a Task Scheduler logon trigger does), and starts the server without a console window.

def startup_script() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"         / f"{TASK_NAME}.vbs"


def server_command(source: str | None, host: str, port: int, extra_hosts: list[str], allow_all: bool) -> list[str]:
    """How autostart launches the server: through uvx for a git/PyPI source, else this same Python."""
    tail = ["serve", "--host", host, "--port", str(port)]
    for h in extra_hosts:
        tail += ["--allowed-host", h]
    if allow_all:
        tail.append("--allow-all-interfaces")
    if source:
        uvx = shutil.which("uvx") or "uvx"
        return [uvx, "--from", source, "opendeck-mcp", *tail]
    return [sys.executable, "-m", "opendeck_mcp", *tail]


def install_autostart(command: list[str]) -> Path:
    if os_name() != "windows":
        raise SystemExit("install-autostart is Windows-only; on Linux use a systemd --user service, "
                         "on macOS a LaunchAgent, running the same command.")
    line = subprocess.list2cmdline(command).replace('"', '""')
    script = startup_script()
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(f'CreateObject("WScript.Shell").Run "{line}", 0, False\r\n'.encode())
    return script


def start_autostart() -> None:
    subprocess.Popen(["wscript.exe", str(startup_script())], close_fds=True)


def uninstall_autostart() -> bool:
    try:
        startup_script().unlink()
        return True
    except FileNotFoundError:
        return False


def running_servers() -> list[int]:
    """PIDs of `opendeck-mcp serve` processes, so they can be stopped."""
    import psutil

    found = []
    for proc in psutil.process_iter(["cmdline"]):
        cmd = proc.info.get("cmdline") or []
        if "serve" in cmd and any("opendeck" in part and "mcp" in part for part in cmd) and proc.pid != os.getpid():
            found.append(proc.pid)
    return found


def stop_servers() -> int:
    import psutil

    pids = running_servers()
    for pid in pids:
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass
    return len(pids)
