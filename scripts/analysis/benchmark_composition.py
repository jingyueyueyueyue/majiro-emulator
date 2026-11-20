"""Measure the generated Android compositor on Windows x64 and compare full frame hashes.

Run before and after with the same --rounds and --images directory. Pass the
before JSON via --compare on the second run. Source page writes, validation
hashes and image loading are outside the timed present command.
"""
import argparse
import ctypes
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time

from PIL import Image

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("build", "convert")]
from build_player_core import msvc_environment
from generate_portable_pixels import generate
from port_common import ROOT, archive_dir, sha256, write_json


class Provider:
    def __init__(self, dll):
        self.api = ctypes.CDLL(str(dll))
        self.api.bench_install.argtypes = []
        self.api.bench_install.restype = None
        self.api.bench_command.argtypes = [ctypes.c_char_p]
        self.api.bench_command.restype = ctypes.c_char_p
        self.api.bench_frame.argtypes = [ctypes.POINTER(ctypes.c_size_t)]
        self.api.bench_frame.restype = ctypes.c_void_p
        self.api.bench_input.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_char_p]
        self.api.bench_input.restype = None
        self.api.bench_install()

    def __call__(self, request):
        reply = self.api.bench_command(request.encode()).decode()
        if not reply.startswith("OK"):
            raise RuntimeError(f"{request.splitlines()[0]}: {reply}")
        return reply

    def frame_hash(self, width, height):
        size = ctypes.c_size_t()
        address = self.api.bench_frame(ctypes.byref(size))
        assert size.value == width * height * 4
        return hashlib.sha256(ctypes.string_at(address, size.value)).hexdigest()


def build(folder, source, headers):
    env = msvc_environment()
    compiler = Path(env["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
    binary = folder / "composition_bench.dll"
    command = [str(compiler), "/nologo", "/LD", "/O2", "/EHsc", "/std:c++20", "/MT",
               "/I" + str(headers), "/I" + str(ROOT / "player/core"), str(source),
               str(Path(__file__).with_name("composition_bench_host.cpp")), "/Fe:" + str(binary)]
    result = subprocess.run(command, cwd=folder, env=env, capture_output=True)
    (folder / (folder.name[:8] + "_compile.log")).write_bytes(result.stdout + result.stderr)
    result.check_returncode()
    return Provider(binary)


def measure(api, width, height, scene, rounds):
    api("reset")
    for handle, alpha in ((1, 1), (2, 0), (3, 1)):
        api(f"create {handle} {width} {height} {alpha}")
    api(f"fill 1 0 0 {width} {height} 2109504")
    api(f"fill 2 0 0 {width} {height} 12615744")
    if scene == "external_mask":
        api("alpha 2 4 0")
        api(f"fill 4 0 0 {width} {height} 40")

    def render(i):
        if scene == "bare":
            return "presentview 1 0 0 0 screen"
        background = f"1 0 0 {width} {height} 0 0 1 1 0 0 0"
        count = {"background": 0, "one_layer": 1, "four_layers": 4}.get(scene, 1)
        x = i % 19 if scene == "moving" else 0
        overlay = f"2 0 0 {width} {height} {x} 0 1 1 0 128 0"
        return "\n".join(["presentview 3 0 0 0 screen", background] + [overlay] * count)

    def update(i):
        if scene == "static":
            return
        if scene == "dirty_same":
            api("fill 3 0 0 1 1 1")
        elif scene != "moving":
            api(f"fill 1 0 0 32 8 {16777215 if i % 2 else 0}")

    # Check distinct animation frames independently of the timing loop.
    hashes = []
    for i in range(6):
        update(i)
        api(render(i))
        hashes.append(api.frame_hash(width, height))
    if scene not in ("static", "dirty_same") and len(set(hashes)) < 2:
        raise ValueError(f"fixture did not animate: {scene}")
    for i in range(3):
        update(i)
        api(render(i))
    samples = []
    changed = 0
    before_us = int(api("perf").split()[1])
    for i in range(rounds):
        update(i + 3)
        request = render(i + 3)
        t0 = time.perf_counter_ns()
        answer = api(request)
        samples.append((time.perf_counter_ns() - t0) / 1e6)
        changed += answer == "OK changed"
    total_us = int(api("perf").split()[1]) - before_us
    return {"width": width, "height": height, "scene": scene, "rounds": rounds,
            "compose_ms_mean": round(total_us / (rounds * 1000), 4),
            "present_ms_median": round(statistics.median(samples), 4),
            "present_ms_p95": round(sorted(samples)[math.ceil(rounds * 0.95) - 1], 4),
            "changed": changed, "frame_sha256": hashes}


def real_frames(api, directory):
    inputs = sorted(directory.glob("*.png"))[:17]
    if len(inputs) < 17:
        raise ValueError("17 PNG images required for the real image sweep")
    api("reset")
    width, height = 1280, 720
    api(f"create 1 {width} {height} 1")
    api(f"fill 1 0 0 {width} {height} 3407961")
    results = []
    for i, path in enumerate(inputs):
        with Image.open(path) as src:
            rgba = src.convert("RGBA")
        sw, sh = rgba.size
        api.api.bench_input(sw, sh, rgba.tobytes())
        api("load 2 1 bench")
        api(f"drawmode 2 {i % 5}")
        if i % 2:
            api("alpha 2 4 1")
        # Warm a plain home view, then exercise crop/offset/zoom and the
        # retained borders of the legacy view on consecutive frames.
        row = f"\n2 0 0 {sw} {sh} -7 9 1 1 0 {i * 13} 0"
        overlay = f"\n2 1 1 {sw} {sh} 23 4 .85 1.2 12 53 0"
        digests = []
        for header, layers in (("presentview 1 0 0 0", row),
                               ("presentview 1 2 -3 17", row + overlay),
                               ("presentzoom 1 2 9 -5 731", overlay + row)):
            api(header + " screen" + layers)
            digests.append(api.frame_hash(width, height))
        results.append({"input_sha256": sha256(path), "frames": digests})
        api("release 2")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--source", type=Path, help="previously generated portable provider")
    parser.add_argument("--headers", type=Path, default=ROOT / "player/core")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path, help="before JSON from identical scenarios")
    args = parser.parse_args()
    if args.rounds < 2:
        parser.error("--rounds must be at least 2")
    folder = archive_dir("composition_bench")
    source = args.source.resolve() if args.source else folder / "portable_pixels.cpp"
    if not args.source:
        generate(source)
    api = build(folder, source, args.headers.resolve())
    results = []
    for width, height in ((800, 600), (1280, 720), (1920, 1080)):
        for scene in ("bare", "background", "one_layer", "four_layers", "external_mask",
                      "moving", "dirty_same", "static"):
            row = measure(api, width, height, scene, args.rounds)
            results.append(row)
            print(width, height, scene, row["present_ms_median"], flush=True)
    real = real_frames(api, args.images) if args.images else []
    report = {"scope": "MSVC x64 Android pixel algorithms, in-memory present; excludes SDL/driver",
              "provider_sha256": sha256(source), "results": results, "real_images": real}
    if args.compare:
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))
        assert baseline["real_images"] == real, "real image or output differs"
        keys = ("width", "height", "scene", "rounds", "frame_sha256")
        for before, after in zip(baseline["results"], results, strict=True):
            assert all(before[k] == after[k] for k in keys), (after["scene"], "frames differ")
        report["matches_baseline_sha256"] = sha256(args.compare)
    write_json(args.output or folder / (folder.name[:8] + "_results.json"), report)


if __name__ == "__main__":
    main()
