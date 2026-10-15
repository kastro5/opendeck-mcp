import sys

from opendeck_mcp.core import apps
from opendeck_mcp.tools.read_tools import get_app_rules
from opendeck_mcp.tools.view import View


def test_app_name_falls_back_to_the_file_name(monkeypatch):
    monkeypatch.setattr(apps, "os_name", lambda: "linux")
    assert apps.app_name("/usr/bin/firefox-bin") == "firefox-bin"


def test_app_name_of_a_real_executable():
    assert apps.app_name(sys.executable)  # FileDescription on Windows ("Python"), else the stem


def test_get_app_rules_lists_rules_and_running_names(paths, monkeypatch):
    monkeypatch.setattr(apps, "running_app_names", lambda user_only=True: ["Autodesk Fusion", "Firefox"])
    out = get_app_rules(View(paths))
    assert "Autodesk Fusion, Firefox" in out and "opendeck_default" in out
