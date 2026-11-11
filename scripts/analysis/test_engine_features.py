"""Check generic version/API discovery on real engines and no-EXE/mutated inputs."""
from pathlib import Path
import json
import os
import re
import struct
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,write_json,timestamp,sha256


def main():
    folder=archive_dir("engine_version_features")
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    production=ROOT/"player/android/src/org/majiro/player/EngineFeatures.java"
    fixture=ROOT/"scripts/analysis/EngineFeaturesChecks.java"
    def run(name,args):
        result=subprocess.run(args,cwd=folder,capture_output=True,timeout=40)
        (folder/(name+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(name+" failed: "+str(folder/(name+".txt")))
        return result.stdout.decode("utf8")
    run("compile",[str(java/"javac.exe"),"-encoding","UTF-8","-d",str(folder),str(production),str(fixture)])
    registry=read_json(ROOT/"reports/engine_code_profiles.json")["sources"]
    samples=[(row["sample"],Path(row["source"]))for row in registry]
    source=read_json(ROOT/"config/player_new_samples.json")["games"]
    extra=next(row for row in source if row["id"]=="kiraoto")
    samples.append(("new_v104",next(p for p in(Path(extra["directory"])/"bin").glob("*.exe")if p.name!="uninst.exe")))
    changed=folder/"changed.exe"
    changed.write_bytes(samples[-1][1].read_bytes()+b"arbitrary resource changes are not an engine identity")
    samples.append(("modified_not_whitelisted",changed))
    empty=folder/"empty.exe";empty.write_bytes(b"not an EXE");samples.append(("no_version_hints",empty))
    listing=folder/"inputs.txt";listing.write_text("\n".join(str(p)for _,p in samples)+"\n",encoding="utf8")
    actual=run("run",[str(java/"java.exe"),"-Dfile.encoding=UTF-8","-cp",str(folder),
        "org.majiro.player.EngineFeaturesChecks",str(listing)]).splitlines()
    assert len(actual)==len(samples)
    cases=[]
    for(name,path),value in zip(samples,actual):
        raw=path.read_bytes();expected=set()
        if b"MajiroObj"in raw:
            expected.update("save:"+v.decode()for v in re.findall(rb"MajiroSavV1\.(10[2-5])",raw))
            for api in(0x2bd1709d,0x7e0b8320,0x87a631a6):
                packed=struct.pack("<I",api)
                if b"\x3d"+packed in raw or any(b"\x81"+bytes([reg])+packed in raw for reg in range(0xf8,0x100)):
                    expected.add("api:"+format(api,"x"))
        assert (set(value.split(","))if value else set())==expected,(name,value,expected)
        cases.append({"sample":name,"features":sorted(expected),"status":"passed"})
    assert cases[-2]["features"]==cases[-3]["features"]
    report={"status":"passed","timestamp":timestamp(),"cases":cases,"exe_fingerprint_validation":False,
        "version_104_discovered":True,"evidence":str(folder.relative_to(ROOT)),
        "source_sha256":sha256(production),"device_accessed":False}
    write_json(ROOT/"reports/engine_version_features_tests.json",report);write_json(folder/"report.json",report)
    changed.unlink();empty.unlink()
    print("Engine version/API discovery:",len(cases),"cases; changed EXE remains recognized without hashes")
    return 0


if __name__=="__main__":raise SystemExit(main())
