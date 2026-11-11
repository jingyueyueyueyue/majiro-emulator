"""Exercise the native parser fixtures on an explicitly selected Android device."""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, read_json, timestamp, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    args = parser.parse_args()
    sdk = Path(read_json(ROOT / "reports/android_toolchain.json")["sdk"])
    apk = read_json(ROOT / "reports/android_probe_build.json")
    adb = sdk / "platform-tools/adb.exe"
    folder = archive_dir("android_core_device")

    def run(label, arguments, timeout=90):
        process = subprocess.run([str(adb), "-s", args.serial, *arguments],
                                 capture_output=True, timeout=timeout)
        (folder / (label + ".txt")).write_bytes(process.stdout + process.stderr)
        if process.returncode:
            raise RuntimeError(f"{label} failed: {folder / (label + '.txt')}")
        return process.stdout.decode("utf-8", "replace")

    run("install", ["install", "-r", apk["apk"]])
    output = run("instrumentation", [
        "shell", "am", "instrument", "-w", "-r", "org.majiro.player/.CoreInstrumentation"])
    passed = "INSTRUMENTATION_CODE: -1" in output and "checks passed." in output
    device = {
        "model": run("model", ["shell", "getprop", "ro.product.model"]).strip(),
        "android": run("android", ["shell", "getprop", "ro.build.version.release"]).strip(),
        "abi": run("abi", ["shell", "getprop", "ro.product.cpu.abilist"]).strip(),
        "page_size": int(run("pages", ["shell", "getconf", "PAGESIZE"]).strip()),
    }
    report = {
        "status": "passed" if passed else "failed", "timestamp": timestamp(),
        "serial": args.serial, "device": device, "apk_sha256": apk["sha256"],
        "scope": "native V1/V2/V3 parsing, absent MJO main, RCT/TS00/RC8 pixels and malformed input rejection",
        "game_execution_verified": False, "evidence": str(folder),
    }
    write_json(ROOT / "reports/android_core_device_tests.json", report)
    run("launch", ["shell", "am", "start", "-n", "org.majiro.player/.MainActivity"])
    print("Android native core device tests:", report["status"], device)
    if not passed:
        print(output)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
