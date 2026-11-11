"""Collect local player diagnostics after user testing; never start a game or clear logs."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
from port_common import ROOT, archive_dir, read_json, timestamp, write_json

PACKAGE = "org.majiro.player"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    args = parser.parse_args()
    adb = Path(read_json(ROOT / "reports/android_toolchain.json")["sdk"]) / "platform-tools/adb.exe"
    folder = archive_dir("android_user_logs")
    report = {"schema": 1, "timestamp": timestamp(), "serial": args.serial,
              "gameplay_started": False, "logs_cleared": False, "player_saves_read": False,
              "evidence": str(folder.relative_to(ROOT)), "commands": []}
    def run(name, command, timeout=30):
        result = subprocess.run([str(adb), "-s", args.serial, *command],
                                capture_output=True, timeout=timeout,cwd=folder)
        (folder / (name + ".txt")).write_bytes(result.stdout + result.stderr)
        report["commands"].append({"name": name, "returncode": result.returncode})
        return result
    state = run("device", ["get-state"])
    if state.returncode or state.stdout.strip() != b"device":
        raise RuntimeError("the authorized tablet is not connected")
    run("player_logcat", ["logcat", "-d", "-v", "threadtime", "-t", "10000",
                         "MajiroPlayer:V", "AndroidRuntime:E", "SDL:W", "*:S"])
    run("crash_buffer", ["logcat", "-b", "crash", "-d", "-v", "threadtime", "-t", "1000"])
    run("player_package", ["shell", "dumpsys", "package", PACKAGE])
    run("device_info", ["shell", "getprop", "ro.build.fingerprint"])
    destination = folder / "logs"
    destination.mkdir(exist_ok=True)
    pulled = run("pull_local_logs", ["pull", "/sdcard/Android/data/" + PACKAGE + "/files/logs",
                                     "."], timeout=120)
    if pulled.returncode:
        names = run("internal_log_names", ["shell", "run-as", PACKAGE, "ls", "files/logs"])
        if not names.returncode:
            destination.mkdir(exist_ok=True)
            for raw in names.stdout.decode("utf8", "replace").splitlines()[:12]:
                name = raw.strip()
                if not name.endswith(".log") or any(c not in "0123456789_session.log" for c in name):
                    continue
                result = run("internal_" + name, ["exec-out", "run-as", PACKAGE,
                                                 "cat", "files/logs/" + name])
                if not result.returncode:
                    (destination / name).write_bytes(result.stdout)
    records = []
    for path in destination.glob("*.log") if destination.is_dir() else []:
        records.append({"name": path.name, "bytes": path.stat().st_size})
    report.update(local_logs=records, status="collected" if records else "logcat_only")
    write_json(folder / "collection.json", report)
    bundle = folder / "Majiro-debug.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in folder.rglob("*"):
            if path.is_file() and path != bundle:
                output.write(path, path.relative_to(folder).as_posix())
    report["bundle"] = str(bundle.relative_to(ROOT))
    write_json(ROOT / "reports/android_user_logs.json", report)
    print(json.dumps({"status": report["status"], "local_logs": len(records),
                      "bundle": str(bundle)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
