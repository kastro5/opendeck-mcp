"""Build the OpenDeck plugin (dev.kastro5.opendeck-mcp.sdPlugin).

    uv run --with pyinstaller python scripts/build_plugin.py build      # this OS's binary -> build/bins/<os>/
    uv run python scripts/build_plugin.py package [--bins build/bins]   # -> dist/<plugin>.zip

`build` freezes the server with PyInstaller (one folder, not one file: it starts
faster and trips fewer antivirus heuristics). `package` puts every OS's binary
found under --bins into one plugin folder, fills in the version and zips it.
CI builds on each OS and packages once, so the zip runs on Windows and Linux.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = "dev.kastro5.opendeck-mcp.sdPlugin"
NAME = "opendeck-mcp-plugin"
OSES = {"win32": "windows", "linux": "linux"}


def version() -> str:
    text = (ROOT / "src" / "opendeck_mcp" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def build(out: Path) -> Path:
    os_name = OSES.get(sys.platform)
    if os_name is None:
        sys.exit(f"no plugin build for {sys.platform}")
    work = ROOT / "build" / "pyinstaller"
    entry = work / "entry.py"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("from opendeck_mcp.__main__ import main\n\nmain()\n", encoding="utf-8")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--log-level", "WARN",
        # Console subsystem on purpose: OpenDeck starts plugins with CREATE_NO_WINDOW and logs their output.
        "--name", NAME, "--distpath", str(work / "dist"), "--workpath", str(work / "tmp"),
        "--specpath", str(work),
        "--collect-data", "opendeck_mcp", "--collect-all", "resvg_py", "--collect-data", "certifi",
        "--collect-submodules", "uvicorn", "--collect-submodules", "opendeck_mcp",
        str(entry),
    ], check=True)
    target = out / os_name
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(work / "dist" / NAME, target)
    print(target)
    return target


def package(bins: Path, dist: Path) -> Path:
    found = sorted(p.name for p in bins.iterdir() if p.name in OSES.values()) if bins.is_dir() else []
    if not found:
        sys.exit(f"no binaries under {bins}; run `build` first")
    staging = dist / PLUGIN
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    source = ROOT / "plugin" / PLUGIN
    for item in source.iterdir():
        shutil.copy2(item, staging / item.name)
    manifest = staging / "manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("__VERSION__", version()), encoding="utf-8")
    for os_name in found:
        shutil.copytree(bins / os_name, staging / "bin" / os_name)
    archive = dist / f"{PLUGIN}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(staging.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(dist).as_posix()
            info = zipfile.ZipInfo.from_file(path, name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if "/bin/linux/" in name:
                # Keep the Linux binary and its libraries executable, whichever OS zipped them.
                info.external_attr = 0o100755 << 16
            with open(path, "rb") as handle:
                zf.writestr(info, handle.read())
    print(f"{archive} ({', '.join(found)})")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, default=ROOT / "build" / "bins")
    p = sub.add_parser("package")
    p.add_argument("--bins", type=Path, default=ROOT / "build" / "bins")
    p.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if args.command == "build":
        build(args.out)
    else:
        package(args.bins, args.dist)


if __name__ == "__main__":
    main()
