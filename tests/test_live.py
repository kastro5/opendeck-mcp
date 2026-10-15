import json
import threading

import pytest

from opendeck_mcp.core.profile import ProfileError
from opendeck_mcp.runtime import live, process
from opendeck_mcp.staging import plan
from opendeck_mcp.tools import edit_tools as E
from opendeck_mcp.tools.view import View

DEV = "ulanzi-d200h"


@pytest.fixture
def running(paths, monkeypatch):
    """Pretend OpenDeck runs: record what would be sent to it instead of sending it."""
    sent = []
    monkeypatch.setattr(process, "is_running", lambda _p: True)
    monkeypatch.setattr(process, "flush", lambda _p, sel, timeout=4.0: {d: False for d in sel})
    monkeypatch.setattr(process, "send_message", lambda _p, m: sent.append(m))
    monkeypatch.setattr(live, "send_settings",
                        lambda _p, ctx, s, port=None: sent.append({"event": "setSettings", "context": ctx,
                                                                   "payload": s}))
    return sent


def update(paths, slot="r3c3", mode="auto", **kw):
    return E.update_key(paths, DEV, slot, None, kw.get("state"), kw.get("title"), kw.get("title_style"),
                        kw.get("image"), kw.get("settings_merge"), None, None, kw.get("current_state"), mode)


def test_context_string_is_the_full_five_part_context():
    assert live.context_string("dev", "Folder/P", {"context": "Keypad.6.0"}) == "dev.Folder/P.Keypad.6.0"
    assert live.context_string("dev", "P", {"context": "dev.P.Keypad.0.2"}) == "dev.P.Keypad.0.2"


def test_title_and_settings_go_live_and_are_journaled(paths, running):
    out = update(paths, title="LIVE", settings_merge={"down": "https://example.com"})
    assert out.startswith("Applied live")
    assert [m["event"] for m in running] == ["setTitle", "setSettings"]
    assert running[0] == {"event": "setTitle", "context": f"{DEV}.Default.Keypad.12.0",
                          "payload": {"title": "LIVE", "state": 0}}
    assert running[1]["payload"]["down"] == "https://example.com"
    assert plan.load(paths)["ops"] == []  # nothing staged
    (entry,) = live.read_journal(paths)
    assert entry["after"]["titles"] == {"0": "LIVE"}
    assert entry["before"]["settings"] != entry["after"]["settings"]


def test_undo_live_sends_the_previous_values(paths, running):
    old_title = View(paths).profile(DEV, "Default")["keys"][12]["states"][0]["text"]
    update(paths, title="LIVE")
    running.clear()
    out = E.undo_live(paths, 1)
    assert out.startswith("Undone live")
    assert running == [{"event": "setTitle", "context": f"{DEV}.Default.Keypad.12.0",
                        "payload": {"title": old_title, "state": 0}}]
    assert live.read_journal(paths) == []
    assert E.undo_live(paths, 1) == "There are no live edits to undo."


def test_default_image_restores_the_actions_own(paths, running):
    update(paths, image="default")
    assert running == [{"event": "setImage", "context": f"{DEV}.Default.Keypad.12.0", "payload": {"state": 0}}]


@pytest.mark.parametrize("kw, reason", [
    ({"title_style": {"colour": "#FF0000"}}, "title style"),
    ({"current_state": 0, "title": "x"}, "current state"),
])
def test_restart_only_changes_are_staged(paths, running, kw, reason):
    out = update(paths, **kw)
    assert out.startswith("Not applied live") and reason in out
    assert running == []
    assert any(r["op"] == "update_key" for r in plan.load(paths)["ops"])


def test_not_running_falls_back_to_staging(paths):
    out = update(paths, title="x")
    assert "OpenDeck isn't running" in out and "Staged" in out


def test_live_mode_refuses_instead_of_staging(paths):
    with pytest.raises(ProfileError, match="can't apply this live"):
        update(paths, title="x", mode="live")
    assert plan.load(paths)["ops"] == []


def test_staged_changes_on_the_device_block_live_edits(paths, running):
    update(paths, title="first", mode="staged")
    out = update(paths, title="second")
    assert "staged changes" in out and running == []


def test_send_settings_registers_as_property_inspector(paths):
    from websockets.sync.server import serve

    frames = []

    def handler(ws):
        for message in ws:
            frames.append(json.loads(message))

    with serve(handler, "127.0.0.1", 0) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.socket.getsockname()[1]
        live.send_settings(paths, "dev.P.Keypad.1.0", {"a": 1}, port=port)
        server.shutdown()
    assert frames == [{"event": "registerPropertyInspector", "uuid": "dev.P.Keypad.1.0"},
                      {"event": "setSettings", "context": "dev.P.Keypad.1.0", "payload": {"a": 1}}]
