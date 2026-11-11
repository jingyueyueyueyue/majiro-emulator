"""Independent encrypted fixtures plus bounded real-game decoding in memory."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_arc import Catalogue
from majiro_image import Decoder, MAGIC, image_key, find_image_password
from port_common import ROOT, write_json


class MemoryMember:
    def __init__(self, name, data):
        self.name, self.data, self.identity = name, data, "fixture:" + name
        self.extension = Path(name).suffix

    def read(self):
        return self.data


class MemoryCatalogue:
    def __init__(self, images):
        self.lookup = {name: [MemoryMember(name, data)] for name, data in images.items()}

    @staticmethod
    def key(name):
        return name.lower()

    def get(self, name):
        return self.lookup[name][0]

    def resolve(self, name, extensions=()):
        for candidate in (name, *(name + suffix for suffix in extensions)):
            if candidate in self.lookup:
                return self.get(candidate)
        raise KeyError(name)


def fixture_key(password):
    # The standard CRC32 API provides independent table values:
    # crc_table[i] = crc32(byte(i xor 255)) xor 0xff000000.
    value = zlib.crc32(password)
    table = [zlib.crc32(bytes([i ^ 255])) ^ 0xFF000000 for i in range(256)]
    return b"".join(struct.pack("<I", value ^ table[(i + value) % 256]) for i in range(256))


def image(variant, payload, base=None, width=2):
    header = MAGIC + variant + struct.pack("<III", width, 1, len(payload))
    if base is not None:
        header += struct.pack("<H", len(base) + 1) + base + b"\0"
    return header + payload


class EncryptedImages(unittest.TestCase):
    def test_crc_key_with_independent_oracle(self):
        for password in (b"", b"123456789", b"test-password", bytes([0x82, 0xA0, 0x82, 0xA2])):
            self.assertEqual(image_key(password), fixture_key(password))

    def test_ts00_known_pixels_and_ts01_base_composition(self):
        password = b"fixture-key"
        key = fixture_key(password)
        encrypt = lambda payload: bytes(b ^ key[i % 1024] for i, b in enumerate(payload))
        # Two literal BGR pixels separated by literal-run count zero.
        base = b"\x01\x02\x03\x00\x04\x05\x06"
        overlay = b"\x00\x00\xff\x00\x11\x22\x33"
        files = {
            "base.rct": image(b"TS00", encrypt(base + b"TS")),
            "overlay.rct": image(b"TS01", encrypt(overlay + b"TS"), b"base.rct"),
        }
        decoder = Decoder(MemoryCatalogue(files), image_password=password)
        pixels, _ = decoder.pixels("base.rct")
        self.assertEqual(pixels.tobytes(), bytes([3, 2, 1, 6, 5, 4]))
        pixels, dependencies = decoder.pixels("overlay.rct")
        self.assertEqual(pixels.tobytes(), bytes([3, 2, 1, 0x33, 0x22, 0x11]))
        self.assertEqual(len(dependencies), 2)

    def test_only_exact_ts_suffix_is_accepted(self):
        key = fixture_key(b"fixture")
        for trailer in (b"T", b"TSx", b"xx", b"TS\0"):
            payload = b"\x01\x02\x03\x00\x04\x05\x06" + trailer
            encrypted = bytes(b ^ key[i] for i, b in enumerate(payload))
            decoder = Decoder(MemoryCatalogue({"bad.rct": image(b"TS00", encrypted)}),
                              image_password=b"fixture")
            with self.subTest(trailer=trailer), self.assertRaises(ValueError):
                decoder.pixels("bad.rct")
    def test_wrong_key_and_truncated_basename_fail(self):
        payload = b"\x01\x02\x03\x00\x04\x05\x06"
        key = fixture_key(b"correct")
        encrypted = bytes(b ^ key[i] for i, b in enumerate(payload))
        files = {"base.rct": image(b"TS00", encrypted),
                 "bad.rct": MAGIC + b"TS01" + struct.pack("<III", 2, 1, 0)}
        decoder = Decoder(MemoryCatalogue(files), image_password=b"wrong")
        with self.assertRaises(ValueError):
            decoder.pixels("base.rct")
        with self.assertRaisesRegex(ValueError, "base filename length"):
            decoder.pixels("bad.rct")


def real_samples(manifest):
    rows = []
    for game in manifest["games"]:
        label = Path(game["directory"]).name
        if label not in {"SWITCH", "White", "Amber Quartz", "BOKUTSUBA"}:
            continue
        catalogue = Catalogue(Path(game["directory"]), include_patch=False)
        decoder = Decoder(catalogue)
        selected = {}
        for members in catalogue.lookup.values():
            member = members[0]
            if member.extension != ".rct":
                continue
            with member.archive.open("rb") as stream:
                stream.seek(member.offset)
                header = stream.read(8)
            variant = header[4:8].decode("ascii")
            if variant not in selected:
                selected[variant] = member.name
            if len(selected) >= (2 if label in {"SWITCH", "BOKUTSUBA"} else 4):
                break
        record = {"sample": label, "cases": [], "original_pixel_comparison": "not_run"}
        if any(variant.startswith("TS") for variant in selected):
            password = find_image_password(catalogue)
            record["password_sha256"] = hashlib.sha256(password).hexdigest()
        for variant, name in selected.items():
            decoded, dependencies = decoder.rgba(name)
            # Every decoded source is bounded and checked by the production reader.
            record["cases"].append({
                "variant": variant, "member": name, "size": list(decoded.size),
                "mode": decoded.mode, "pixel_sha256": hashlib.sha256(decoded.tobytes()).hexdigest(),
                "dependencies": [m.identity for m in dependencies],
            })
        rows.append(record)
    if not rows:
        raise ValueError("no configured real image samples")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path)
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(EncryptedImages))
    report = {"status": "passed" if result.wasSuccessful() else "failed",
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors)}
    if result.wasSuccessful() and args.samples:
        report["real_samples"] = real_samples(json.loads(args.samples.read_text(encoding="utf-8")))
    write_json(ROOT / "reports/encrypted_image_tests.json", report)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
