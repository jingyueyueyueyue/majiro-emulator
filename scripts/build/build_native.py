"""Compile our small page provider using the installed MSVC; no SDK copies."""
from __future__ import annotations
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, atomic_bytes, lua_string, read_json, sha256, write_json


def build():
    source = ROOT / "runtime/native/majiro_pixels.cpp"
    receipt = ROOT / "reports/native_build.json"
    inputs={path.relative_to(ROOT).as_posix():sha256(path)for path in
            (source,ROOT/"runtime/native/screen_view.hpp")}
    identity=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode("utf-8")).hexdigest()[:16]
    output=ROOT/"resources/native"/("majiro_pixels_"+identity+".dll")
    manifest=ROOT/"runtime/artemis_native_library.lua"
    manifest_bytes=("return "+lua_string("native/"+output.name)+"\n").encode("utf-8")
    if output.exists() and receipt.exists():
        old = read_json(receipt)
        if old.get("input_sha256")==inputs and old["sha256"] == sha256(output):
            atomic_bytes(manifest,manifest_bytes)
            return old
    vswhere = Path("C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe")
    installations = json.loads(subprocess.check_output(
        [str(vswhere), "-all", "-prerelease", "-products", "*", "-format", "json", "-utf8"],
        encoding="utf-8-sig"))
    candidates = [Path(row["installationPath"]) / "VC/Auxiliary/Build/vcvars64.bat" for row in installations]
    vcvars = next((p for p in candidates if p.exists()), None)
    if vcvars is None:
        raise FileNotFoundError("installed MSVC x64 environment")
    env_result = subprocess.run(f'cmd.exe /d /s /c ""{vcvars}" >nul && set"',
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    environment = {key.upper(): value for key, value in os.environ.items()}
    for line in env_result.stdout.decode("mbcs").splitlines():
        key, sep, value = line.partition("=")
        if sep and key and not key.startswith("="):
            environment[key.upper()] = value
    evidence = archive_dir("native_build")
    executable = evidence / "majiro_pixels.dll"
    compiler = Path(environment["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
    args = [str(compiler), "/nologo", "/LD", "/O2", "/EHsc", "/std:c++17", "/MT", "/utf-8",
            str(source), "/Fo" + str(evidence / "majiro_pixels.obj"), "/Fe" + str(executable),
            "/link", "windowscodecs.lib", "ole32.lib", "uuid.lib","gdi32.lib","user32.lib","bcrypt.lib","comdlg32.lib",
            "/IMPLIB:" + str(evidence / "majiro_pixels.lib")]
    result = subprocess.run(args, cwd=evidence, env=environment, capture_output=True)
    (evidence / "20261108_compile.txt").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"native compilation failed: {evidence}")
    try:
        atomic_bytes(output, executable.read_bytes())
    finally:
        # Rebuildable binaries are never retained as failed game backups.
        for suffix in (".dll",".obj", ".lib", ".exp"):
            intermediate = evidence / ("majiro_pixels" + suffix)
            if intermediate.exists():
                intermediate.unlink()
    record = {"schema": 1, "status": "passed", "platform": "windows-x64",
              "source_sha256": sha256(source), "sha256": sha256(output),
              "input_sha256":inputs,
              "library":str(output),
              "bytes": output.stat().st_size, "vcvars": str(vcvars),
              "build_evidence": str(evidence), "android_status": "not_implemented"}
    write_json(receipt, record)
    atomic_bytes(manifest,manifest_bytes)
    return record


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=True))
