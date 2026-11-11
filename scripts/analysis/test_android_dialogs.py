"""Run Android modal-dialog ownership and lifecycle rules on the debug JVM."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json


def run():
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])
    folder=archive_dir("android_dialog_lifecycle");classes=folder/"classes";classes.mkdir()
    source=ROOT/"player/android/src/org/majiro/player/ModalDialogState.java"
    fixture=ROOT/"scripts/analysis/ModalDialogChecks.java"
    compile=subprocess.run([str(java/"bin/javac.exe"),"-encoding","UTF-8","-source","8","-target","8",
        "-d",str(classes),str(source),str(fixture)],capture_output=True,timeout=60)
    (folder/(folder.name[:8]+"_javac.txt")).write_bytes(compile.stdout+compile.stderr)
    if compile.returncode:raise RuntimeError("dialog JVM compilation failed: "+str(folder))
    result=subprocess.run([str(java/"bin/java.exe"),"-cp",str(classes),
        "org.majiro.player.ModalDialogChecks"],capture_output=True,timeout=30)
    (folder/(folder.name[:8]+"_java.txt")).write_bytes(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError("dialog JVM checks failed: "+str(folder))
    report=json.loads(result.stdout)
    report.update(schema=1,timestamp=timestamp(),source_sha256=sha256(source),
        fixture_sha256=sha256(fixture),device_execution=False,evidence=str(folder.relative_to(ROOT)),
        scope="production async dialog request ownership, lifecycle suspension, duplicate/stale replies, "
              "destroy/error delivery and button answer validation")
    write_json(ROOT/"reports/android_dialog_tests.json",report)
    print(f"Android dialog ownership: {report['checks']} JVM checks passed")
    return 0


if __name__=="__main__":raise SystemExit(run())
