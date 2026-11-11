"""Compare current shared copy algorithms with the archived pre-phase3 provider."""
from pathlib import Path
import json
import statistics
import subprocess
import sys

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/name)for name in("build","convert")]
from build_player_core import msvc_environment
from generate_portable_pixels import generate
from port_common import ROOT,archive_dir,sha256,timestamp,write_json


def run():
    folder=archive_dir("copy_benchmark")
    candidates=sorted((ROOT/"archive/2026-11/20261111_emulator_audit_graphics/phase2_transactions").rglob("portable_pixels.cpp"))
    baseline=next(path for path in candidates if "Original uniform-alpha MMX kernel"not in path.read_text(encoding="utf8"))
    current=folder/"current.cpp";generate(current)
    environment=msvc_environment()
    compiler=Path(environment["VCTOOLSINSTALLDIR"])/"bin/Hostx64/x64/cl.exe"
    rows={}
    for name,source in(("before",baseline),("after",current)):
        binary=folder/(name+".exe")
        command=[str(compiler),"/nologo","/O2","/EHsc","/std:c++20","/MT","/utf-8",
                 "/I"+str(ROOT/"player/core"),str(source),
                 str(ROOT/"scripts/analysis/player_copy_benchmark.cpp"),"/Fe:"+str(binary)]
        build=subprocess.run(command,cwd=folder,env=environment,capture_output=True)
        (folder/(name+"_compile.log")).write_bytes(build.stdout+build.stderr)
        if build.returncode:raise RuntimeError("copy benchmark compilation failed: "+str(folder))
        result=subprocess.run([str(binary)],cwd=folder,capture_output=True,timeout=120)
        (folder/(name+"_run.log")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError("copy benchmark failed: "+str(folder))
        rows[name]=json.loads(result.stdout)
    comparisons=[]
    for before,after in zip(rows["before"],rows["after"]):
        assert before["case"]==after["case"]and before["rgba_fnv1a64"]==after["rgba_fnv1a64"]
        old=statistics.median(before["milliseconds_per_command"])
        new=statistics.median(after["milliseconds_per_command"])
        pixels=1919*1079 if before["case"]=="self_copy"else 1920*1080
        comparisons.append({"case":before["case"],"rgba_fnv1a64":after["rgba_fnv1a64"],
                            "before_ms":old,"after_ms":new,"speedup":old/new,
                            "removed_temporary_bytes":pixels*5})
    report={"status":"passed","timestamp":timestamp(),"comparisons":comparisons,"raw":rows,
            "baseline":str(baseline),"baseline_sha256":sha256(baseline),
            "current_generated_sha256":sha256(current),
            "scope":"1920x1080 Windows MSVC release standalone shared-provider CPU benchmark; full output checksums match",
            "limits":"not Android device performance; timing varies with system load"}
    write_json(folder/"report.json",report);write_json(ROOT/"reports/player_copy_benchmark.json",report)
    print(comparisons,flush=True)
    return report


if __name__=="__main__":run()
