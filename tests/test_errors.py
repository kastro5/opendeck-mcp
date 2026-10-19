import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from opendeck_mcp import server


def _call(name, args):
    return asyncio.run(server.mcp.call_tool(name, args))


def test_our_errors_reach_the_agent(paths):
    # The SDK hides the text of any exception that isn't a ToolError.
    with pytest.raises(ToolError, match="unknown device 'no-such-deck'"):
        _call("show_profile", {"device": "no-such-deck"})


def test_missing_opendeck_says_where_it_looked(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDECK_CONFIG_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setattr(server, "_paths", None)
    with pytest.raises(ToolError, match="OPENDECK_CONFIG_DIR"):
        _call("get_overview", {})
