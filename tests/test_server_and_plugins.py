import io
import zipfile

import pytest

from opendeck_mcp.plugins import installer


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


MANIFEST = b'{"Name":"Z","Author":"a","Version":"1","Icon":"i","OS":[{"Platform":"windows"}],"Actions":[]}'


def test_unpack_finds_plugin_folder_and_unwraps_nested_zip(paths, tmp_path):
    inner = _zip({"com.z.thing.sdPlugin/manifest.json": MANIFEST, "com.z.thing.sdPlugin/a/b.txt": b"x"})
    outer = tmp_path / "release.zip"
    outer.write_bytes(_zip({"com.z.thing.streamDeckPlugin": inner}))
    unpacked = installer.unpack(paths, outer)
    assert unpacked.folder == "com.z.thing.sdPlugin"
    assert (unpacked.path / "a" / "b.txt").read_bytes() == b"x"


def test_unpack_rejects_zip_slip(paths, tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(_zip({"com.z.thing.sdPlugin/manifest.json": MANIFEST,
                          "com.z.thing.sdPlugin/../../evil.txt": b"x"}))
    with pytest.raises(installer.InstallError, match="unsafe"):
        installer.unpack(paths, bad)


@pytest.mark.anyio
async def test_server_lists_and_calls_tools(paths):
    from mcp.client import Client

    from opendeck_mcp.server import mcp

    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        for name in ("get_overview", "show_profile", "set_key", "apply_changes", "restore_backup", "stage_plugin"):
            assert name in tools
        assert tools["get_overview"].annotations.read_only_hint is True
        assert tools["apply_changes"].annotations.destructive_hint is True
        result = await client.call_tool("show_profile", {"device": "ulanzi-d200h"})
        text = result.content[0].text
        assert "r1c1" in text and "Multi Action" in text
        result = await client.call_tool("get_plugin_settings", {"plugin": "Home Assistant"})
        assert "«redacted»" in result.content[0].text and "eyJ" not in result.content[0].text
