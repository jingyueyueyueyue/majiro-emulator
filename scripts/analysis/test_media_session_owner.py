"""Concurrent checks for the production media ownership gate without Android hardware."""
from pathlib import Path
import json
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json


def main():
    folder=archive_dir("media_session_owner")
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    source=ROOT/"player/android/src/org/majiro/player/MediaSessionOwner.java"
    result=subprocess.run([str(java/"javac.exe"),"-encoding","UTF-8","-d",str(folder),
        str(source),str(ROOT/"scripts/analysis/MediaSessionOwnerChecks.java")],capture_output=True,timeout=30)
    (folder/"compile.txt").write_bytes(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError("media ownership fixture compile failed")
    result=subprocess.run([str(java/"java.exe"),"-cp",str(folder),"org.majiro.player.MediaSessionOwnerChecks"],
        capture_output=True,timeout=30)
    (folder/"run.txt").write_bytes(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError("media ownership regression: "+str(folder/"run.txt"))
    report=json.loads(result.stdout.decode("utf8"))
    report.update(timestamp=timestamp(),scope="100 real thread cancellation/reentry cycles plus ordered teardown, pending replacement and failed-open cases",
        source_sha256=sha256(source),device_accessed=False,evidence=str(folder.relative_to(ROOT)))
    write_json(ROOT/"reports/media_session_owner_tests.json",report)
    print(json.dumps(report,ensure_ascii=False))
    return 0


if __name__=="__main__":raise SystemExit(main())
