"""Exercise the generated production pixel core across interrupted save transactions and reentry."""
from pathlib import Path
import json
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"build"))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from build_player_core import msvc_environment
from generate_portable_pixels import generate
from port_common import ROOT,archive_dir,sha256,timestamp,write_json


def main():
    folder=archive_dir("pixel_reentry");generated=folder/"pixels.cpp";generate(generated)
    env=msvc_environment();compiler=Path(env["VCTOOLSINSTALLDIR"])/"bin/Hostx64/x64/cl.exe"
    command=[str(compiler),"/nologo","/O2","/EHsc","/std:c++20","/utf-8",
        "/I"+str(ROOT/"player/core"),str(generated),str(ROOT/"scripts/analysis/pixel_reentry_probe.cpp"),
        "/Fe:"+str(folder/"probe.exe")]
    built=subprocess.run(command,cwd=folder,env=env,capture_output=True,timeout=60)
    (folder/"compile.txt").write_bytes(built.stdout+built.stderr)
    if built.returncode:raise RuntimeError("pixel probe compilation: "+str(folder/"compile.txt"))
    result=subprocess.run([str(folder/"probe.exe")],cwd=folder,capture_output=True,timeout=10)
    (folder/"run.txt").write_bytes(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError("pixel reentry failed: "+str(folder/"run.txt"))
    report=json.loads(result.stdout.decode("utf8"))
    report.update(timestamp=timestamp(),source_sha256=sha256(ROOT/"player/core/portable_pixels_wrapper.inc"),
        evidence=str(folder.relative_to(ROOT)),device_accessed=False,
        scope="20 production native sessions, interrupted checkpoint cleanup and empty-state rollback")
    write_json(ROOT/"reports/pixel_reentry_tests.json",report)
    print(json.dumps(report,ensure_ascii=False))
    return 0


if __name__=="__main__":raise SystemExit(main())
