"""Compile shared image/host contract fixture after the final source freeze."""
import json
from pathlib import Path
import subprocess
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / name) for name in ("build", "convert")]
from build_player_core import msvc_environment
from generate_portable_pixels import generate
from port_common import ROOT, archive_dir, write_json


def run():
    folder = archive_dir("player_graphics_contract")
    generated = folder / "portable_pixels.cpp"
    generate(generated)
    environment = msvc_environment()
    compiler = Path(environment["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
    binary = folder / "contract.exe"
    command = [str(compiler), "/nologo", "/O2", "/EHsc", "/std:c++20", "/MT", "/utf-8",
               "/I" + str(ROOT / "player/core"), str(generated),
               str(ROOT / "player/core/majiro_image.cpp"),
               str(ROOT / "scripts/analysis/player_graphics_contract_probe.cpp"),
               "/Fe:" + str(binary)]
    result = subprocess.run(command, cwd=folder, env=environment, capture_output=True)
    (folder / "compile.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("graphics contract compilation failed: " + str(folder))
    result = subprocess.run([str(binary)], cwd=folder, capture_output=True)
    (folder / "run.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("graphics contract verification failed: " + str(folder))
    report = json.loads(result.stdout)
    write_json(ROOT / "reports/player_graphics_contract_tests.json", report)
    print(report)
    return report


if __name__ == "__main__":
    run()
