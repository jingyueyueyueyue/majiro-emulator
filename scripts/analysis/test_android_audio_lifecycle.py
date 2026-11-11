"""Compile and execute the actual Android audio lifecycle class on the debug JVM."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,timestamp,write_json


def run():
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])
    folder=archive_dir("android_audio_lifecycle")
    classes=folder/"classes";classes.mkdir()
    production=ROOT/"player/android/src/org/majiro/player/AudioLifecycle.java"
    fixture=ROOT/"scripts/analysis/AudioLifecycleChecks.java"
    compile=subprocess.run([str(java/"bin/javac.exe"),"-encoding","UTF-8","-source","8","-target","8",
        "-d",str(classes),str(production),str(fixture)],capture_output=True,timeout=60)
    (folder/(folder.name[:8]+"_javac.txt")).write_bytes(compile.stdout+compile.stderr)
    if compile.returncode:raise RuntimeError("audio lifecycle Java compilation failed: "+str(folder))
    process=subprocess.run([str(java/"bin/java.exe"),"-cp",str(classes),
        "org.majiro.player.AudioLifecycleChecks"],capture_output=True,timeout=30)
    (folder/(folder.name[:8]+"_java.txt")).write_bytes(process.stdout+process.stderr)
    if process.returncode:raise RuntimeError("audio lifecycle checks failed: "+str(folder))
    result=json.loads(process.stdout)
    report={"schema":1,"timestamp":timestamp(),"status":result["status"],"checks":result["checks"],
        "production_sha256":hashlib.sha256(production.read_bytes()).hexdigest(),
        "fixture_sha256":hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "evidence":str(folder.relative_to(ROOT)),
        "scope":"production Java ownership logic, script/lifecycle pause and intro-to-loop handoff",
        "limits":["deterministic player fixture; Android decoder/device behavior remains separate"],
        "device_execution":False}
    write_json(ROOT/"reports/android_audio_lifecycle_tests.json",report)
    print("Android audio lifecycle:",report["checks"],"JVM checks passed")
    return 0


if __name__=="__main__":raise SystemExit(run())
