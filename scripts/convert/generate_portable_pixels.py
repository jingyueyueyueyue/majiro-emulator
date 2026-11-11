"""Reuse tested pixel algorithms with Android I/O and raster callbacks."""
from __future__ import annotations
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from port_common import ROOT, atomic_bytes


def section(text, start, end):
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError("pixel source anchors changed; inspect before regenerating")
    return text[text.index(start):text.index(end)]


def generate(output):
    source = (ROOT / "player/native/majiro_pixels.cpp").read_text(encoding="utf-8")
    wrapper = (ROOT / "player/core/portable_pixels_wrapper.inc").read_text(encoding="utf-8")
    body = section(source, "struct Page {", "static ComPtr<IWICImagingFactory> factory()")
    # The image/file/Windows utility region sits between require() and used().
    begin = body.index("static void checked(")
    end = body.index("static size_t used()")
    body = body[:begin] + body[end:]
    body = body.replace("static std::vector<std::wstring> private_fonts;\n", "")
    algorithm = section(source, "static void composite(", "static std::wstring save_path(")
    basic = section(source, '    if(op=="create")', '    if(op=="glyphs")')
    advanced = section(source, '    if(op=="modify")', 'extern "C" __declspec(dllexport) char*')
    # Advanced includes the terminal throw/brace of run().
    generated = wrapper.replace("/*PAGE_ALGORITHMS*/", body + "\n" + algorithm)
    generated = generated.replace("/*BASIC_OPERATIONS*/", basic)
    generated = generated.replace("/*ADVANCED_OPERATIONS*/", advanced)
    atomic_bytes(Path(output), generated.encode("utf-8"))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: generate_portable_pixels.py <generated-output.cpp>")
    generate(Path(sys.argv[1]))
