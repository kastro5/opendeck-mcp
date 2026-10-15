import httpx
import pytest

from opendeck_mcp import http as H

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
ACCEPT = {"Accept": "application/json, text/event-stream"}


def test_token_is_generated_once_and_can_be_reset(paths, monkeypatch):
    monkeypatch.delenv("OPENDECK_MCP_TOKEN", raising=False)
    first = H.get_token(paths)
    assert len(first) >= 32 and H.get_token(paths) == first
    assert H.get_token(paths, reset=True) != first
    monkeypatch.setenv("OPENDECK_MCP_TOKEN", "x" * 40)
    assert H.get_token(paths) == "x" * 40


@pytest.mark.parametrize("host, ok", [("127.0.0.1", True), ("100.77.1.2", True), ("localhost", True),
                                      ("0.0.0.0", False), ("::", False), ("my-pc", False)])
def test_bind_rules(host, ok):
    if ok:
        H.check_bind(host, False)
    else:
        with pytest.raises(SystemExit):
            H.check_bind(host, False)
    H.check_bind("0.0.0.0", True)


@pytest.mark.anyio
async def test_requests_need_the_token_and_a_known_host(paths):
    app = H.build_app("100.77.1.2", "s3cret-token", ["my-pc.tailnet.ts.net"])
    from opendeck_mcp.server import mcp

    async with mcp.session_manager.run() if hasattr(mcp, "session_manager") else _nullcontext():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://100.77.1.2:8765") as client:
            assert (await client.post("/mcp", json=INIT, headers=ACCEPT)).status_code == 401
            bad = {**ACCEPT, "Authorization": "Bearer wrong"}
            assert (await client.post("/mcp", json=INIT, headers=bad)).status_code == 401
            good = {**ACCEPT, "Authorization": "Bearer s3cret-token"}
            evil = {**good, "Host": "evil.example:8765"}
            assert (await client.post("/mcp", json=INIT, headers=evil)).status_code == 421


class _nullcontext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def test_client_command_and_server_command():
    assert H.client_command("100.1.2.3", 8765, "T") == (
        'claude mcp add --transport http opendeck http://100.1.2.3:8765/mcp --header "Authorization: Bearer T"')
    assert "[fd7a::1]:8765" in H.client_command("fd7a::1", 8765, "T")
    cmd = H.server_command("git+https://x/y@v1", "100.1.2.3", 9000, ["pc.ts.net"], False)
    assert cmd[1:4] == ["--from", "git+https://x/y@v1", "opendeck-mcp"]
    assert cmd[4:] == ["serve", "--host", "100.1.2.3", "--port", "9000", "--allowed-host", "pc.ts.net"]


def test_autostart_script_runs_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(H, "os_name", lambda: "windows")
    script = H.install_autostart([r"C:\Program Files\uv\uvx.exe", "--from", "x", "opendeck-mcp", "serve"])
    text = script.read_bytes().decode()
    assert script.parent.name == "Startup"
    assert text == ('CreateObject("WScript.Shell").Run """C:\\Program Files\\uv\\uvx.exe"" --from x opendeck-mcp '
                    'serve", 0, False\r\n')
    assert H.uninstall_autostart() and not script.exists()
