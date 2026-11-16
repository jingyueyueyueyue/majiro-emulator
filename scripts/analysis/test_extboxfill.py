"""Check reverse-alpha fills, including a copied console mask and its sprite."""
from pathlib import Path
import json
import sys

from test_screen_semantics import Portable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, sha256, timestamp, write_json


def run():
    folder = archive_dir("extboxfill")
    api = Portable(folder)
    cases = 0
    try:
        for depth in (8, 32):
            for anti in (-10, 0, 1, 64, 128, 254, 255, 300):
                api("reset")
                api(f"create 1 3 1 0 {depth}")
                for x, value in enumerate((0, 100, 200)):
                    api(f"set 1 {x} 0 {value * 0x010101}")
                api(f"extfill 1 0 0 3 1 255 {anti}")
                bounded = max(0, min(255, anti))
                color = (255, 255, 255) if depth == 8 else (255, 0, 0)
                for x, old in enumerate((0, 100, 200)):
                    rgba = list(map(int, api(f"pixel 1 {x} 0")[0].split()[1:]))
                    expected = [
                        (old * (bounded + 1) + fill * (256 - bounded)) >> 8
                        for fill in color
                    ]
                    assert rgba == [*expected, 255], (depth, anti, x, rgba, expected)
                    cases += 1
        # Console initialization copies a reverse-alpha mask, adjusts it,
        # then copies the whole page to the live text/toolbar sprite.
        api("reset")
        api("create 1 4 2 0")
        api("create 2 4 2 0 8")
        api("antidata 1 2")
        api("fill 1 0 0 4 2 65280")
        api("fill 2 0 0 4 2 255")
        api("set 2 1 0 0")
        api("set 2 2 1 128")
        api("extfill 2 0 0 9999 9999 255 255")
        api("create 3 4 2 0")
        api("alpha 3 4 0")
        api("copy 1 0 0 4 2 3 0 0")
        assert api("pixel 3 1 0")[0] == "OK 0 255 0 255"
        assert api("pixel 3 2 1")[0] == "OK 0 255 0 127"
        assert api("pixel 3 0 0")[0] == "OK 0 255 0 0"
        api("create 5 4 2 0")
        api("fill 5 0 0 4 2 255")
        _, pixels = api("present 5 screen\n3 0 0 4 2 0 0 1 1 0 0 0")
        assert pixels[4:8] == bytes((0, 255, 0, 255)), "toolbar pixel disappeared"
        assert pixels[:4] == bytes((255, 0, 0, 255)), "transparent margin changed"
        assert pixels[24:28] == bytes((128, 127, 0, 255)), "window opacity changed"
        cases += 1
    finally:
        api.close()
    report = {
        "status": "passed", "timestamp": timestamp(), "cases": cases,
        "scope": "production portable pixel provider: indexed/RGB reverse-alpha fills, "
                 "clamping and console mask copy through final sprite composition",
        "native_source_sha256": sha256(ROOT / "player/native/majiro_pixels.cpp"),
        "evidence": str(folder),
    }
    write_json(folder / "report.json", report)
    write_json(ROOT / "reports/extboxfill_tests.json", report)
    print(json.dumps({"status": "passed", "cases": cases}))
    return report


if __name__ == "__main__":
    run()
