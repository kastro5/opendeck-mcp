"""Make a real OpenDeck profile safe to commit as a test fixture.

    uv run python scripts/scrub_fixture.py <profile.json> <out.json>

Every settings string becomes a placeholder (keys, types and structure stay),
so entity ids, URLs and the like don't leak. Byte format is preserved.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from opendeck_mcp.core.jsonio import dumps, read_json  # noqa: E402


def scrub(value, key=""):
    if isinstance(value, dict):
        return {k: scrub(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, key) for v in value]
    if isinstance(value, str) and value:
        if value.startswith(("http://", "https://")):
            return f"https://example.com/{key}"
        return f"<{key}>"
    return value


def main() -> None:
    doc = read_json(Path(sys.argv[1]))
    for array in ("keys", "sliders", "infobars"):
        for inst in doc.get(array) or []:
            for item in [inst] + list((inst or {}).get("children") or []):
                if item:
                    item["settings"] = scrub(item.get("settings"))
    Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True)
    Path(sys.argv[2]).write_bytes(dumps(doc))


if __name__ == "__main__":
    main()
