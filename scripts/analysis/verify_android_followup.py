"""Verify the fully audited APK, then run real Android glyph/core/video probes."""
from pathlib import Path
import argparse
import json
import re
import shlex
import subprocess
import sys
from urllib.parse import quote

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/part)for part in("analysis","convert")]
from audit_player_semantics import source_identity
from port_common import ROOT,archive_dir,read_json,sha256,timestamp,write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial",default="ea615a82")
    parser.add_argument("--install",action="store_true")
    args=parser.parse_args()
    audit=read_json(ROOT/"reports/emulator_audit_validation.json")
    package=read_json(ROOT/"reports/android_probe_build.json")
    assert audit["status"]=="passed"and audit["source_identity"]==source_identity()
    assert audit["apk"]["sha256"]==package["sha256"]==sha256(Path(package["apk"]))
    application=package["application_id"]
    assert application=="com.tyranor.runtime.majiro"
    folder=archive_dir("android_followup")
    base=["adb","-s",args.serial]
    report={"status":"running","timestamp":timestamp(),"application_id":application,
            "apk_sha256":package["sha256"],"version_name":package["version_name"],
            "version_code":package["version_code"],"cases":[],
            "evidence":str(folder.relative_to(ROOT)),"full_routes_verified":False}
    def run(label,*parts,timeout=120):
        command=[*base,*parts]
        if parts and parts[0]=="shell":
            command=[*base,"shell",shlex.join(parts[1:])]
        result=subprocess.run(command,capture_output=True,timeout=timeout)
        (folder/(label+".txt")).write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(label+" failed: "+str(folder))
        return result.stdout.decode("utf8","replace")
    def publish():
        write_json(folder/"report.json",report)
        write_json(ROOT/"reports/android_followup.json",report)
    publish()
    try:
        run("stop","shell","am","force-stop",application)
        if args.install:run("install","install","-r",package["apk"])
        path=run("package_path","shell","pm","path",application).strip().removeprefix("package:")
        installed=run("installed_hash","shell","sha256sum",path).split()[0]
        assert installed==package["sha256"],"installed APK does not match final audited bytes"
        report["installed_apk_verified"]=True
        run("wake","shell","input","keyevent","224")
        run("unlock","shell","wm","dismiss-keyguard")
        cases=[("core","CoreInstrumentation",None),("font","RenderInstrumentation",None)]
        for game,movie in (("happy_princess","movie/opening.mpg"),
                           ("Amber Quartz","movie/AQ_OP800_H.mpg"),
                           ("RURINOKASANE","movie/Ruri_OP.wmv")):
            root="primary:Download/MajiroGames"
            uri=("content://com.android.externalstorage.documents/tree/"+quote(root,safe="")+
                 "/document/"+quote(root+"/"+game+"/"+movie,safe=""))
            cases.append(("movie_"+game.replace(" ","_"),"MovieInstrumentation",uri))
        for name,instrumentation,uri in cases:
            command=["shell","am","instrument","-r","-w"]
            if uri:command+=["-e","movie_uri",uri]
            command+=[application+"/org.majiro.player."+instrumentation]
            output=run(name,*command,timeout=120)
            match=re.search(r"INSTRUMENTATION_CODE:\s*(-?\d+)",output)
            row={"name":name,"status":"passed"if match and int(match[1])==-1 else"failed"}
            for key,value in re.findall(r"^INSTRUMENTATION_RESULT: ([^=]+)=(.*)$",output,re.M):
                if key=="cases":continue
                value=value.rstrip("\r")
                row[key]=int(value)if re.fullmatch(r"-?\d+",value)else value
            if "decoded_frames"in row:
                row["decoded_fps"]=round(row["decoded_frames"]*1000/max(1,row["sample_ms"]),2)
            report["cases"].append(row);publish()
            print(json.dumps(row,ensure_ascii=True),flush=True)
            if row["status"]!="passed":raise RuntimeError(name+" failed: "+str(folder))
        report["status"]="passed"
    except Exception as error:
        report.update(status="failed",error=str(error))
        raise
    finally:
        report["completed_at"]=timestamp();publish()
    return 0


if __name__=="__main__":raise SystemExit(main())
