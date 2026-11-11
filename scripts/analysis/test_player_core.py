"""Compare the portable native parser against independent fixtures and all local archives."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, read_json, write_json
from test_formats import arc, mjo


def probe(binary, path, passed=True):
    result = subprocess.run([str(binary), str(path)], capture_output=True, timeout=30)
    if not result.stdout:
        raise AssertionError("native probe returned no JSON: " + result.stderr.decode("utf-8", "replace"))
    report = json.loads(result.stdout)
    if passed:
        assert result.returncode == 0 and report["status"] == "passed", (path, report)
    else:
        assert result.returncode != 0 and report["status"] == "failed", (path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=ROOT / "config/player_samples.json")
    args = parser.parse_args()
    binary = ROOT / "resources/player/majiro_probe.exe"
    if not binary.is_file():
        parser.error("build_player_core.py must run first")
    folder = archive_dir("native_core_fixtures")
    code = struct.pack("<HiH", 0x800, 0, 0x82B)
    payload = mjo(code)
    fixtures = []
    prefix = folder.name[:8]
    for version in (1, 2, 3):
        path = folder / f"{prefix}_arc_v{version}.arc"
        valid = arc(version, [(b"start.mjo", payload), (b"functions.mjo", mjo(code, main=0xFFFFFFFF))])
        path.write_bytes(valid)
        record = probe(binary, path)
        assert record["parsed_scripts"] == 2 and record["scripts_without_main"] == 1
        assert record["script_instructions"] == 4
        fixtures.append({"version": version, "status": "passed"})
        for length in (0, 20, len(valid) - 1):
            bad = folder / f"{prefix}_truncated_v{version}_{length}.arc"
            bad.write_bytes(valid[:length])
            probe(binary, bad, passed=False)
        empty = folder / f"{prefix}_empty_v{version}.arc"
        empty.write_bytes(arc(version, []))
        assert probe(binary, empty)["members"] == 0
    programs = [
        (mjo(struct.pack("<HiHH", 0x82C, 1, 0x191, 0x82B)), False),
        (mjo(struct.pack("<H", 0xFFFF)), False),
        (mjo(code, main=0xFFFFFFFE), False),
        (mjo(code, main=0xFFFFFFFF, entries=((1, 1),)), False),
        (mjo(struct.pack("<HIIHH", 0x80F, 1, 1, 0, 0x82B)), False),
    ]
    for index, (script, passed) in enumerate(programs):
        path = folder / f"{prefix}_bad_script_{index}.arc"
        path.write_bytes(arc(2, [(b"bad.mjo", script)]))
        probe(binary, path, passed)
    inventory = read_json(ROOT / "reports/majiro_samples.json")
    rows = []
    for game in inventory["games"]:
        paths = Path(game["directory"])
        for item in game["archives"]:
            path = paths / item["name"]
            actual = probe(binary, path)
            scripts = [s for s in game["scripts"] if s["archive"] == path.name]
            assert actual["arc_version"] == item["version"], (path, actual)
            assert actual["members"] == item["members"], (path, actual)
            assert actual["parsed_scripts"] == len(scripts), (path, actual)
            assert actual["script_instructions"] == sum(s["instructions"] for s in scripts), (path, actual)
            rows.append({"sample": paths.name, "archive": path.name, **actual})
    write_json(ROOT / "reports/player_core_tests.json", {
        "status": "passed", "fixtures": fixtures, "malformed_programs": len(programs),
        "archives": rows, "native_archive_count": len(rows),
        "native_script_count": sum(row["parsed_scripts"] for row in rows),
        "scope": "same C++ sources used by Android; desktop parser differential verification",
        "android_execution_verified": False, "playable": False,
    })
    print("Native core: independent malformed fixtures and", len(rows), "real archives passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
