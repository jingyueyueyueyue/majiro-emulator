"""Known-pixel image compression fixtures and real non-story title dependencies."""
from __future__ import annotations
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_image import Decoder, unpack
from port_common import ROOT, write_json


class ImageTests(unittest.TestCase):
    def test_rgb_overlapping_run(self):
        pixel = bytes((25, 50, 100))
        self.assertEqual(unpack(pixel + b"\x82", 4, 1, 3), pixel * 4)

    def test_indexed_overlapping_run(self):
        self.assertEqual(unpack(b"\x22\x80", 4, 1, 1), b"\x22" * 4)

    def test_independent_literal_pixel_fixture(self):
        rand = random.Random(12017)
        rgb = rand.randbytes(90)
        encoded = rgb[:3] + bytes((28,)) + rgb[3:]
        self.assertEqual(unpack(encoded, 10, 3, 3), rgb)

    def test_invalid_runs_rejected(self):
        for data in (b"", b"\0\0", b"\0\0\0\xff", b"\0\0\0\x82x"):
            with self.assertRaises(ValueError):
                unpack(data, 4, 1, 3)

    def test_real_title_base_and_mask(self):
        decoder = Decoder()
        background, _ = decoder.rgba("title_back")
        composite, dependencies = decoder.rgba("title_base")
        self.assertEqual(background.size, (1280, 720))
        self.assertEqual(composite.size, background.size)
        self.assertGreaterEqual(len(dependencies), 2)
        self.assertGreater(composite.getextrema()[0][1], 0)
        self.assertTrue(any(m.name == "title_back.rct" for m in dependencies))
        for name in ("title_back", "title_base", "title_dai_off"):
            image, sources = decoder.rgba(name)
            self.assertEqual(image.size, (1280, 720))
            self.assertGreater(len(sources), 0)


def run():
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ImageTests))
    report = {"status": "passed" if result.wasSuccessful() else "failed",
              "tests": result.testsRun, "errors": len(result.errors), "failures": len(result.failures),
              "native_original_pixel_comparison": "not_run"}
    write_json(ROOT / "reports/image_tests.json", report)
    if not result.wasSuccessful():
        raise RuntimeError("image regression failed")
    return report


if __name__ == "__main__":
    run()
