"""Check portable RCT/RC8 pixels against the independent Python decoder."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_arc import Catalogue
from majiro_image import Decoder, find_image_password
from port_common import ROOT, archive_dir, read_json, write_json
from test_formats import arc
from test_encrypted_images import fixture_key, image, real_samples


def main():
    binary = ROOT / "resources/player/majiro_image_probe.exe"
    folder = archive_dir("portable_image_fixtures")
    cases = []

    def check(label, raw_members, password, expected, dimensions):
        index = len(cases)
        path, output = folder / f"{folder.name[:8]}_{index}.arc", folder / f"{index}.rgba"
        path.write_bytes(arc(3, raw_members))
        result = subprocess.run(
            [str(binary), str(path), str(output), password.hex() if password is not None else "-"],
            capture_output=True, timeout=30)
        if result.returncode:
            raise AssertionError((label, result.stderr.decode("utf-8", "replace")))
        metadata = json.loads(result.stdout)
        actual = output.read_bytes()
        assert [metadata["width"], metadata["height"]] == list(dimensions), (label, metadata)
        assert actual == expected, (label, hashlib.sha256(actual).hexdigest(), hashlib.sha256(expected).hexdigest())
        cases.append({
            "sample": label, "size": list(dimensions),
            "pixel_sha256": hashlib.sha256(actual).hexdigest(),
            "source_sha256": [hashlib.sha256(data).hexdigest() for _, data in raw_members],
        })
        output.unlink()

    password = b"fixture-key"
    key = fixture_key(password)
    encrypted = lambda data: bytes(value ^ key[i % 1024] for i, value in enumerate(data))
    base, overlay = b"\x01\x02\x03\x00\x04\x05\x06", b"\x00\x00\xff\x00\x11\x22\x33"
    check("independent_TS00", [(b"base.rct", image(b"TS00", encrypted(base + b"TS")))],
          password, bytes([3, 2, 1, 255, 6, 5, 4, 255]), [2, 1])
    check("independent_TS01_base", [
        (b"overlay.rct", image(b"TS01", encrypted(overlay + b"TS"), b"base.rct")),
        (b"base.rct", image(b"TS00", encrypted(base + b"TS")))],
        password, bytes([3, 2, 1, 255, 0x33, 0x22, 0x11, 255]), [2, 1])
    palette = bytes(component for i in range(256) for component in (i, i, i))
    mask = image(b"8_00", palette + b"\x00\x00\x80")
    # Case-insensitive archive lookup must be identical for the root image
    # and its mask, including CP932 pairs containing ASCII-looking trails.
    for stem in (b"BUTTON", bytes((0x83, 0x5c)) + b"BUTTON"):
        check("case_normalized_mask_" + stem.hex(), [
            (stem + b".RCT", image(b"TC00", base)),
            (stem.lower() + b"_.RC8", mask)],
            None, bytes([3, 2, 1, 255, 6, 5, 4, 127]), [2, 1])
    colored_palette = bytes(component for i in range(256) for component in (255-i, 7, i//2))
    check("mask_uses_index_not_palette", [
        (b"color.rct", image(b"TC00", base)),
        (b"color_.rc8", image(b"8_00", colored_palette + b"\x00\x00\x80"))],
        None, bytes([3, 2, 1, 255, 6, 5, 4, 127]), [2, 1])
    manifest = read_json(ROOT / "config/player_samples.json")
    # The unit fixture suite can rewrite its report concurrently. Derive this
    # test's real-game inputs directly from its manifest, not another receipt.
    records = real_samples(manifest)
    for record in records:
        game = next(g for g in manifest["games"] if g["id"] == record["sample"])
        catalogue = Catalogue(Path(game["directory"]), include_patch=False)
        decoder = Decoder(catalogue)
        password = (find_image_password(catalogue)
                    if any(c["variant"].startswith("TS") for c in record["cases"]) else None)
        for case in record["cases"]:
            pixels, dependencies = decoder.rgba(case["member"])
            check(record["sample"] + ":" + case["variant"],
                  [(m.raw_name, m.read()) for m in dependencies],
                  password, pixels.convert("RGBA").tobytes(), pixels.size)
    write_json(ROOT / "reports/player_image_tests.json", {
        "status": "passed", "cases": cases, "case_count": len(cases),
        "scope": "portable C++ and Python RGBA equality; same C++ compiled for Android ARM64",
        "original_executable_pixel_comparison": False, "android_execution_verified": False,
    })
    print("Portable image core:", len(cases), "independent and real-game pixel comparisons passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
