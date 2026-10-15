"""The plugin's settings page, opened in the browser by the Plugins tab's gear button.

OpenDeck has no settings UI for plugins: the gear only sends `showSettingsInterface`.
So the plugin serves this page itself, on its own short-lived listener: 127.0.0.1 and a
random port, under a random path that only the opened URL knows (it shows the token, and
the path doubles as the forms' CSRF guard). The listener closes after LIFETIME seconds.
"""

from __future__ import annotations

import html
import logging
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from . import __version__
from . import http as H

if TYPE_CHECKING:
    from .plugin import Plugin

log = logging.getLogger("opendeck_mcp.settings_page")
LIFETIME = 600
README = "https://github.com/kastro5/opendeck-mcp#as-an-opendeck-plugin"

STYLE = """
:root { color-scheme: light dark; --bg:#f6f6f4; --card:#fff; --text:#1c1c1c; --muted:#666; --line:#ddd;
        --accent:#2f6fde; --bad:#c0392b; --good:#2e8b57; --code:#f0f0ee; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#171717; --card:#212121; --text:#eee; --muted:#aaa; --line:#383838; --accent:#6b9cff;
          --bad:#ff6b5e; --good:#5fd08f; --code:#2a2a2a; } }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui, sans-serif; }
main { max-width:720px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:16px; margin:0 0 8px; }
.sub { color:var(--muted); margin:0 0 20px; }
section { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin:0 0 16px; }
.ok { color:var(--good); font-weight:600; } .bad { color:var(--bad); font-weight:600; }
pre { background:var(--code); padding:12px; border-radius:8px; white-space:pre-wrap; word-break:break-all;
      margin:8px 0; font-size:13px; }
label { display:block; margin:8px 0 2px; font-weight:600; font-size:14px; }
input, select { width:100%; box-sizing:border-box; padding:7px 9px; border:1px solid var(--line);
      border-radius:6px; background:var(--bg); color:var(--text); font:inherit; }
button { margin-top:12px; padding:7px 14px; border:0; border-radius:6px; background:var(--accent); color:#fff;
      font:inherit; cursor:pointer; } button.plain { background:transparent; color:var(--accent);
      border:1px solid var(--line); }
.note { color:var(--muted); font-size:13px; margin:6px 0 0; } .msg { padding:10px 12px; border-radius:8px;
      border:1px solid var(--line); margin:0 0 16px; } a { color:var(--accent); }
"""


def render(plugin: Plugin, message: str = "", error: bool = False) -> str:
    from .plugin import local_addresses

    e = html.escape
    listener = plugin.listener
    config = listener.config
    state = listener.state
    if state.get("state") == "listening":
        status = f'<span class="ok">Serving</span> on <code>{e(listener.url)}</code>'
    else:
        status = f'<span class="bad">Not serving</span>: {e(str(state.get("error") or state.get("state")))}'
    command = H.client_command(config["host"], config["port"], H.get_token(plugin.paths))
    local_only = config["host"].startswith("127.") or config["host"] == "::1"
    where = ("Run it on this computer. To use Claude Code on another machine, pick a network address "
             "below (a Tailscale address is the safe choice)." if local_only else
             "Run it on the machine where you use Claude Code; it must be able to reach this address.")
    options = "".join(
        f'<option value="{e(a)}"{" selected" if a == config["host"] else ""}>{e(a)} ({e(n)})</option>'
        for a, n in local_addresses())
    if config["host"] not in {a for a, _ in local_addresses()}:
        options += f'<option value="{e(config["host"])}" selected>{e(config["host"])} (not on this PC now)</option>'
    banner = f'<p class="msg {"bad" if error else "ok"}">{e(message)}</p>' if message else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>OpenDeck MCP</title>
<style>{STYLE}</style></head><body><main>
<h1>OpenDeck MCP</h1><p class="sub">Version {e(__version__)} · lets an AI assistant set up your deck</p>
{banner}
<section><h2>Status</h2><p>{status}</p></section>
<section><h2>Connect Claude Code</h2><p class="note">{e(where)}</p>
<pre id="cmd">{e(command)}</pre>
<button type="button" onclick="navigator.clipboard.writeText(document.getElementById('cmd').textContent)
.then(()=>{{this.textContent='Copied'}})">Copy</button>
<p class="note">The command contains a secret token: anyone who has it and can reach the address can change
your deck. Other MCP clients: use the URL with an <code>Authorization: Bearer</code> header.</p></section>
<section><h2>Address</h2><form method="post" action="bind">
<label for="host">Listen on</label><select id="host" name="host">{options}</select>
<label for="port">Port</label><input id="port" name="port" inputmode="numeric" value="{config['port']}">
<label for="extra">Extra host names (optional, comma-separated)</label>
<input id="extra" name="allowed_hosts" value="{e(', '.join(config['allowed_hosts']))}"
 placeholder="e.g. my-pc.tailnet-name.ts.net">
<p class="note">For a name clients use instead of the IP, such as a Tailscale MagicDNS name.</p>
<button>Save and restart the listener</button></form></section>
<section><h2>Token</h2><p class="note">Make a new token if the old one leaked. Clients using the old
one stop working until you run the new command on them.</p>
<form method="post" action="reset" onsubmit="return confirm('Make a new token?')">
<button class="plain">Make a new token</button></form></section>
<p class="note">This page closes itself after 10 minutes; reopen it with the gear in OpenDeck's Plugins tab.
<a href="{README}" target="_blank" rel="noreferrer">Help</a></p>
</main></body></html>"""


class SettingsPage:
    def __init__(self, plugin: Plugin):
        self.plugin = plugin
        self.secret = secrets.token_urlsafe(18)
        page = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # keep tokens and paths out of the plugin log
                pass

            def _route(self) -> str | None:
                prefix = f"/{page.secret}/"
                return self.path[len(prefix):] if self.path.startswith(prefix) else None

            def _send(self, status: int, body: str = "", location: str | None = None) -> None:
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self._route() != "":
                    return self._send(404, "Not found")
                self._send(200, render(page.plugin))

            def do_POST(self) -> None:
                route = self._route()
                if route not in ("bind", "reset"):
                    return self._send(404, "Not found")
                length = min(int(self.headers.get("Content-Length") or 0), 10_000)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                try:
                    if route == "bind":
                        page.plugin.change_server(form)
                        message = "Saved. The listener restarted; update clients if the address changed."
                    else:
                        page.plugin.reset_token()
                        message = "New token made. Run the new command on each client."
                    self._send(200, render(page.plugin, message))
                except ValueError as exc:
                    self._send(400, render(page.plugin, str(exc), error=True))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/{self.secret}/"
        self.timer = threading.Timer(LIFETIME, self.close)
        self.timer.daemon = True

    def open(self) -> None:
        threading.Thread(target=self.server.serve_forever, daemon=True, name="settings-page").start()
        self.timer.start()
        opened = webbrowser.open(self.url)
        log.info("settings page on 127.0.0.1:%d, browser %s", self.server.server_address[1],
                 "opened" if opened else "didn't open")

    def close(self) -> None:
        self.timer.cancel()

        def stop() -> None:
            self.server.shutdown()
            self.server.server_close()

        threading.Thread(target=stop, daemon=True).start()
