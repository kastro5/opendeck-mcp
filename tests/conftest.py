import shutil
from pathlib import Path

import pytest

from opendeck_mcp.core.paths import Paths

FIXTURE = Path(__file__).parent / "fixtures" / "config"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def paths(tmp_path, monkeypatch) -> Paths:
    """A private copy of the fixture OpenDeck config, with OpenDeck 'not running'."""
    config = tmp_path / "opendeck"
    shutil.copytree(FIXTURE, config)
    data = tmp_path / "mcp-data"
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setenv("OPENDECK_CONFIG_DIR", str(config))
    monkeypatch.setenv("OPENDECK_MCP_DATA_DIR", str(data))
    monkeypatch.setenv("OPENDECK_LOG_DIR", str(logs))
    monkeypatch.setenv("OPENDECK_EXE", str(tmp_path / "no-opendeck.exe"))
    from opendeck_mcp import server

    monkeypatch.setattr(server, "_paths", None)
    return Paths(config_dir=config, log_dir=logs, exe=tmp_path / "no-opendeck.exe", data_dir=data)
