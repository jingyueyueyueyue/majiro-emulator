"""Build the portable core as an Android ARM64 library and a PC debug probe."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from majiro_mjo import opcode_table
from port_common import ROOT, archive_dir, atomic_bytes, read_json, sha256, timestamp, write_json


def msvc_environment():
    where = Path("C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe")
    installs = json.loads(subprocess.check_output(
        [str(where), "-all", "-prerelease", "-products", "*", "-format", "json", "-utf8"],
        encoding="utf-8-sig"))
    environments = [
        Path(row["installationPath"]) / "VC/Auxiliary/Build/vcvars64.bat" for row in installs]
    vcvars = next(path for path in environments if path.is_file())
    process = subprocess.run(
        f'cmd.exe /d /s /c ""{vcvars}" >nul && set"', capture_output=True, check=True)
    env = {key.upper(): value for key, value in os.environ.items()}
    for line in process.stdout.decode("mbcs").splitlines():
        key, sep, value = line.partition("=")
        if sep and key and not key.startswith("="):
            env[key.upper()] = value
    return env


def core_inputs():
    paths={*(ROOT/"player/core").glob("*.cpp"),*(ROOT/"player/core").glob("*.hpp"),
           ROOT/"player/android/native_bridge.cpp",ROOT/"references/majiro-py/opcodes.py",
           ROOT/"scripts/build/build_player_core.py"}
    return {path.relative_to(ROOT).as_posix():sha256(path)for path in sorted(paths)}


def build(android=False):
    inputs=core_inputs()
    evidence = archive_dir("player_core_build")
    definitions = opcode_table()
    generated = (
        "#pragma once\n#include <array>\n#include <cstdint>\n"
        "namespace majiro {\nstruct Opcode { uint16_t code; const char* encoding; };\n"
        f"inline constexpr std::array<Opcode, {len(definitions)}> kOpcodes = {{{{\n"
        + ",\n".join(f'    {{{code}, "{encoding}"}}'
                    for code, (_, encoding) in sorted(definitions.items()))
        + "\n}};\n}\n")
    (evidence / "majiro_opcodes.hpp").write_text(generated, encoding="ascii")
    core = ROOT / "player/core/majiro_core.cpp"
    images = ROOT / "player/core/majiro_image.cpp"
    saves = ROOT / "player/core/majiro_save.cpp"
    story_saves=ROOT/"player/core/majiro_story_save.cpp"
    image_cli = ROOT / "player/core/image_probe_main.cpp"
    cli = ROOT / "player/core/probe_main.cpp"
    env = msvc_environment()
    compiler = Path(env["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
    executable = evidence / "majiro_probe.exe"
    command = [
        str(compiler), "/nologo", "/O2", "/EHsc", "/std:c++20", "/MT", "/utf-8",
        "/I" + str(evidence), str(core), str(images), str(cli),
        "/Fo" + str(evidence) + os.sep, "/Fe" + str(executable),
    ]
    result = subprocess.run(command, cwd=evidence, env=env, capture_output=True)
    (evidence / "compile_pc.txt").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("PC core compilation failed: " + str(evidence / "compile_pc.txt"))
    destination = ROOT / "resources/player/majiro_probe.exe"
    atomic_bytes(destination, executable.read_bytes())
    executable.unlink()
    image_executable = evidence / "majiro_image_probe.exe"
    image_command = [
        str(compiler), "/nologo", "/O2", "/EHsc", "/std:c++20", "/MT", "/utf-8",
        "/I" + str(evidence), str(core), str(images), str(image_cli),
        "/Fo" + str(evidence) + os.sep, "/Fe" + str(image_executable),
    ]
    result = subprocess.run(image_command, cwd=evidence, env=env, capture_output=True)
    (evidence / "compile_pc_image.txt").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("PC image core compilation failed: " + str(evidence / "compile_pc_image.txt"))
    image_destination = ROOT / "resources/player/majiro_image_probe.exe"
    atomic_bytes(image_destination, image_executable.read_bytes())
    image_executable.unlink()
    for path in evidence.glob("*.obj"):
        path.unlink()
    report = {
        "schema": 1, "timestamp": timestamp(), "status": "passed",
        "primary_target": "android-arm64", "pc_role": "debug_probe",
        "probe": str(destination), "probe_sha256": sha256(destination),
        "source_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in (
            core, ROOT / "player/core/majiro_core.hpp", images,saves,ROOT/"player/core/majiro_save.hpp",
            story_saves,ROOT/"player/core/majiro_story_save.hpp",
            ROOT / "player/core/majiro_image.hpp", image_cli, cli,
            ROOT / "references/majiro-py/opcodes.py")},
        "build_evidence": str(evidence), "android": "not_built", "playable": False,
        "image_probe": str(image_destination), "image_probe_sha256": sha256(image_destination),
        "input_sha256":inputs,
    }
    if android:
        config = read_json(ROOT / "reports/android_toolchain.json")
        if config["status"] != "ready":
            raise RuntimeError("Android build prerequisites are not ready")
        sdk = Path(config["sdk"])
        ndk = sdk / "ndk/28.2.13676358/toolchains/llvm/prebuilt/windows-x86_64"
        clang = ndk / "bin/clang++.exe"
        bridge = ROOT / "player/android/native_bridge.cpp"
        output = evidence / "libmajiro_core.so"
        args = [
            str(clang), "--target=aarch64-linux-android26", "-std=c++20", "-O2",
            "-fPIC", "-shared", "-static-libstdc++", "-fvisibility=hidden",
            "-Wl,-z,max-page-size=16384", "-I" + str(evidence), str(core), str(images),str(saves),
            str(story_saves),str(bridge),
            "-o", str(output),
        ]
        result = subprocess.run(args, cwd=evidence, capture_output=True)
        (evidence / "compile_android.txt").write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError("Android core compilation failed: " + str(evidence / "compile_android.txt"))
        library = ROOT / "resources/player/android/arm64-v8a/libmajiro_core.so"
        atomic_bytes(library, output.read_bytes())
        output.unlink()
        report.update(android="cross_compiled", android_library=str(library),
                      android_library_sha256=sha256(library), minimum_android_api=26,
                      android_page_alignment=16384, device_verified=False)
    if inputs!=core_inputs():
        raise RuntimeError("native core inputs changed during compilation; rebuild before packaging")
    write_json(ROOT / "reports/player_core_build.json", report)
    print("PC debug probe:", destination)
    print("Android:", report["android"])
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--android", action="store_true")
    args = parser.parse_args()
    build(args.android)
