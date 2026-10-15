import asyncio
import json
import sys
import urllib.error
import urllib.request

import pytest

from opendeck_mcp import plugin as P
from opendeck_mcp import server
from opendeck_mcp.core.devices import layouts
from opendeck_mcp.runtime import detached, process
from opendeck_mcp.settings_page import SettingsPage

# -- the plugin process ------------------------------------------------------------- #

@pytest.mark.parametrize("given, error", [
    ({"host": "127.0.0.1", "port": "8765"}, None),
    ({"host": "100.77.1.2", "port": 9000, "allowed_hosts": "a.ts.net, b"}, None),
    ({"host": "0.0.0.0"}, "every network"),
    ({"host": "my-pc"}, "IP address"),
    ({"host": "127.0.0.1", "port": "80"}, "between"),
    ({"host": "127.0.0.1", "port": "x"}, "number"),
])
def test_server_settings_are_checked(given, error):
    if error is None:
        config = P.check_server(given)
        assert isinstance(config["port"], int) and isinstance(config["allowed_hosts"], list)
    else:
        with pytest.raises(ValueError, match=error):
            P.check_server(given)


class FakeSocket:
    def __init__(self, incoming):
        self.incoming = [json.dumps(m) for m in incoming]
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        return self.incoming.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_plugin_registers_records_devices_and_starts_the_listener(paths, monkeypatch):
    started = []
    monkeypatch.setattr(P.Listener, "start", lambda self, config: started.append(config))
    info = {"devices": [{"id": "ulanzi-d200h", "name": "Ulanzi D200H", "size": {"rows": 3, "columns": 5},
                         "type": 0}]}
    stored = {"devices": {"old-deck": {"rows": 2, "columns": 3, "connected": True}},
              "server": {"host": "127.0.0.1", "port": 8800}}
    socket = FakeSocket([
        {"event": "deviceDidConnect", "device": "other", "deviceInfo": {"size": {"rows": 1, "columns": 2}}},
        {"event": "didReceiveGlobalSettings", "payload": {"settings": stored}},
        {"event": "deviceDidDisconnect", "device": "ulanzi-d200h"},
    ])
    import websockets

    monkeypatch.setattr(websockets, "connect", lambda *a, **k: socket)
    plugin = P.Plugin(paths, "dev.kastro5.opendeck-mcp.sdPlugin", info)
    asyncio.run(plugin.run(57116, "registerPlugin"))

    assert socket.sent[0] == {"event": "registerPlugin", "uuid": "dev.kastro5.opendeck-mcp.sdPlugin"}
    assert socket.sent[1]["event"] == "getGlobalSettings"
    saved = socket.sent[-1]["payload"]
    assert saved["devices"]["old-deck"]["connected"] is False  # remembered, not yet seen this run
    assert saved["devices"]["other"]["columns"] == 2  # the event that came before the settings
    assert saved["devices"]["ulanzi-d200h"]["connected"] is False
    assert started == [{"host": "127.0.0.1", "port": 8800, "allowed_hosts": []}]

    # devices.py reads the plugin's record like the bridge's
    settings = paths.settings_dir / "dev.kastro5.opendeck-mcp.sdPlugin.json"
    settings.write_text(json.dumps(saved), encoding="utf-8")
    assert layouts(paths)["other"].columns == 2


def test_a_taken_port_is_reported_not_fatal(paths):
    import socket

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    try:
        listener = P.Listener(paths)
        listener.start({"host": "127.0.0.1", "port": holder.getsockname()[1], "allowed_hosts": []})
        assert listener.state["state"] == "error" and "in use" in listener.state["error"]
        assert "not serving" in P.describe_status(paths)
    finally:
        holder.close()


def test_frozen_exe_started_by_opendeck_runs_the_plugin(monkeypatch):
    from opendeck_mcp import __main__

    seen = []
    monkeypatch.setattr(P, "main", lambda argv: seen.append(argv))
    __main__.main(["-port", "57116", "-pluginUUID", "x"])
    assert seen == [["-port", "57116", "-pluginUUID", "x"]]


# -- the settings page ---------------------------------------------------------------- #

class PagePlugin:
    def __init__(self, paths):
        self.paths = paths
        self.listener = P.Listener(paths)
        self.listener.state = {"state": "listening"}
        self.changes = []

    def change_server(self, form):
        if form.get("port") == "1":
            raise ValueError("the port must be between 1024 and 65535")
        self.changes.append(form)

    def reset_token(self):
        self.changes.append("reset")


def _request(url, data=None):
    try:
        with urllib.request.urlopen(url, data=data, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_settings_page_only_answers_its_secret_path(paths, monkeypatch):
    monkeypatch.setenv("OPENDECK_MCP_TOKEN", "t" * 40)
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    fake = PagePlugin(paths)
    page = SettingsPage(fake)
    page.open()
    try:
        base = page.url.rsplit("/", 2)[0]
        assert _request(base + "/")[0] == 404
        status, body = _request(page.url)
        assert status == 200 and "t" * 40 in body and "claude mcp add" in body
        assert _request(base + "/bind", b"host=127.0.0.1&port=9000")[0] == 404
        status, _ = _request(page.url + "bind", b"host=127.0.0.1&port=9000&allowed_hosts=")
        assert status == 200 and fake.changes[-1]["port"] == "9000"
        status, body = _request(page.url + "bind", b"host=127.0.0.1&port=1")
        assert status == 400 and "between" in body
        assert _request(page.url + "reset", b"")[0] == 200 and fake.changes[-1] == "reset"
    finally:
        page.close()


# -- applying from inside OpenDeck ------------------------------------------------------ #

def test_helper_report_is_handed_over_once(paths, monkeypatch):
    monkeypatch.setattr(detached, "_spawn", lambda command, log: None)
    text = detached.start(paths, "apply", show_window=False)
    assert "next opendeck tool call" in text
    assert "still running" in detached.take_report(paths)

    from opendeck_mcp.runtime import applier

    monkeypatch.setattr(applier, "apply", lambda *a, **k: "Applied 1 change(s).")
    job = paths.data_dir / "apply_job.json"
    assert detached.run_job(paths, job) == 0 and not job.exists()
    assert "Applied 1 change(s)." in detached.take_report(paths)
    assert detached.take_report(paths) is None


def test_failed_helper_reports_and_restarts_opendeck(paths, monkeypatch):
    monkeypatch.setattr(detached, "_spawn", lambda command, log: None)
    detached.start(paths, "restore", backup_id="x")
    from opendeck_mcp.runtime import applier

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(applier, "restore", boom)
    monkeypatch.setattr(process, "is_running", lambda p: False)
    monkeypatch.setattr(process, "start", lambda p, hidden: True)
    assert detached.run_job(paths, paths.data_dir / "apply_job.json") == 1
    report = detached.take_report(paths)
    assert "disk full" in report and "OpenDeck was restarted" in report


def test_tools_carry_the_report_in_plugin_mode(paths, monkeypatch):
    monkeypatch.setattr(detached, "_spawn", lambda command, log: None)
    detached.start(paths, "apply")
    detached._write(paths, {"id": "j", "kind": "apply", "status": "done", "text": "Applied 2 change(s)."})
    assert "Applied 2" not in server.list_backups()  # not the plugin: nothing attached
    monkeypatch.setenv(detached.ENV, "1")
    assert server.list_backups().startswith("Result of the last apply_changes (job j):\nApplied 2")
    assert "Applied 2" not in server.list_backups()


def test_plugin_mode_apply_goes_through_the_helper(paths, monkeypatch):
    spawned = []
    monkeypatch.setattr(detached, "_spawn", lambda command, log: spawned.append(command))
    monkeypatch.setenv(detached.ENV, "1")
    server.set_key("ulanzi-d200x", "r1c5", "Open URL", settings={"url": "https://example.com"})
    out = server.apply_changes(confirm=True)
    assert "Applying now" in out and spawned and spawned[0][-2:] == ["--job", str(paths.data_dir / "apply_job.json")]
    if not getattr(sys, "frozen", False):
        assert spawned[0][1:4] == ["-m", "opendeck_mcp", "apply-helper"]


def test_bare_exe_names_never_match_a_plugin_folder(paths, monkeypatch, tmp_path):
    monkeypatch.chdir(paths.plugins_dir)
    assert process._norm("Registry") == ""
    assert process._norm(str(tmp_path)) != ""
