"""Native pixel correctness, overlap, alpha, clipping and failure bounds."""
from __future__ import annotations
import json
from pathlib import Path
import sys

from native_pixels import Pixels
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, sha256, write_json,native_pixels_library


def run():
    evidence = archive_dir("native_pixels")
    api = Pixels()
    cases = []
    assert api("create 1 4 1 1") == "OK"
    assert api("pixel 1 0 0") == "OK 0 0 0 0"
    assert api("fill 1 0 0 1 1 255") == "OK"
    assert api("fill 1 1 0 1 1 65280") == "OK"
    assert api("fill 1 2 0 1 1 16711680") == "OK"
    cases.append("create and RGB channel order")
    assert api("copy 1 0 0 3 1 1 1 0") == "OK"
    assert [api(f"pixel 1 {x} 0") for x in range(4)] == [
        "OK 255 0 0 255", "OK 255 0 0 255", "OK 0 255 0 255", "OK 0 0 255 255"]
    cases.append("overlapping copies freeze source")
    assert api("create 2 3 1 1") == "OK"
    assert api("copy 1 0 0 4 1 2 -1 0") == "OK"
    assert api("pixel 2 0 0") == "OK 255 0 0 255"
    assert api("pixel 2 2 0") == "OK 0 0 255 255"
    cases.append("negative destination clipping")
    assert api("create 3 1 1 1") == "OK"
    assert api("copy 3 0 0 1 1 2 0 0") == "OK"
    assert api("pixel 2 0 0") == "OK 0 0 0 0"
    cases.append("copy preserves zero alpha rather than blending")
    path = evidence / "20261108_pixels.png"
    assert api("png 2 " + str(path)) == "OK"
    from PIL import Image
    with Image.open(path) as image:
        assert image.size == (3, 1)
        assert image.convert("RGBA").tobytes() == bytes((0, 0, 0, 0, 0, 255, 0, 255, 0, 0, 255, 255))
    assert api("load 4 1 " + str(path)) == "OK"
    assert api("pixel 4 2 0") == "OK 0 0 255 255"
    cases.append("PNG file round trip through WIC")
    for request in ("create 5 32768 32768 1", "pixel 1 9 0", "copy 99 0 0 1 1 1 0 0",
                    "create 1 1 1 1", "invalid"):
        assert api(request).startswith("ERR "), request
    cases.append("invalid handles, dimensions and operations fail closed")
    assert api("create 5 3 1 0 8") == "OK"
    assert api("fill 5 0 0 1 1 255") == "OK"
    assert api("antidata 2 5") == "OK"
    assert api("pixel 2 0 0") == "OK 0 0 0 0"
    assert api("pixel 2 1 0") == "OK 0 255 0 255"
    assert api("release 5") == "OK"
    assert api("pixel 2 1 0") == "OK 0 255 0 255"
    assert api("antidata 2 0") == "OK"
    assert api("pixel 5 0 0").startswith("ERR ")
    cases.append("indexed alpha association and dependency lifetime")
    assert api("reset") == "OK" and api("stats") == "OK 0 0"
    report = {"status": "passed", "cases": cases, "tests": len(cases),
              "dll_sha256": sha256(native_pixels_library()),
              "evidence": str(evidence), "original_engine_comparison": "not_run"}
    write_json(ROOT / "reports/native_pixel_tests.json", report)
    print(json.dumps({"status": "passed", "tests": len(cases)}))
    return report


if __name__ == "__main__":
    run()
