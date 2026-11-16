"""Install an explicitly supplied personal MS Gothic collection into one app."""
from pathlib import Path
import argparse
import hashlib
import json
import shlex
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font",type=Path,required=True)
    parser.add_argument("--serial",default="ea615a82")
    args=parser.parse_args()
    raw=args.font.read_bytes()
    if raw[:4]!=b"ttcf"or len(raw)>32*1024*1024:
        raise ValueError("a bounded TTC font collection is required")
    from fontTools.ttLib import TTCollection
    fonts=TTCollection(args.font)
    names=[{n.toUnicode()for n in face["name"].names if n.nameID==1}for face in fonts.fonts]
    if len(names)<3 or"MS Gothic"not in names[0]or"MS PGothic"not in names[2]:
        raise ValueError("this collection does not provide the MS Gothic/PGothic face order")
    folder=archive_dir("android_personal_font")
    package="com.tyranor.runtime.majiro";base=["adb","-s",args.serial]
    def shell(*parts,input=None):
        result=subprocess.run([*base,"shell",shlex.join(parts)],input=input,
                              capture_output=True,timeout=60)
        if result.returncode:raise RuntimeError(result.stderr.decode("utf8","replace"))
        return result.stdout.decode("utf8","replace")
    shell("run-as",package,"mkdir","-p","files/fonts")
    pending="files/fonts/msgothic.ttc.writing"
    expected=hashlib.sha256(raw).hexdigest()
    remote="/data/local/tmp/"+folder.name+"_msgothic.ttc"
    try:
        result=subprocess.run([*base,"push",str(args.font),remote],capture_output=True,timeout=60)
        (folder/"push.txt").write_bytes(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError("font staging failed")
        # Keep the binary pipe on Android: Windows adb shell stdin can stop at
        # a text-mode EOF byte even when subprocess receives a bytes object.
        pipeline="cat "+shlex.quote(remote)+" | run-as "+package+" dd of="+pending+" 2>/dev/null"
        shell("sh","-c",pipeline)
        actual=shell("run-as",package,"sha256sum",pending).split()[0]
        if actual!=expected:raise RuntimeError("font transfer content differs")
        shell("run-as",package,"mv",pending,"files/fonts/msgothic.ttc")
    finally:
        shell("rm","-f",remote)
        shell("run-as",package,"rm","-f",pending)
    report={"status":"passed","timestamp":timestamp(),"package":package,
            "source":str(args.font.resolve()),"sha256":expected,"bytes":len(raw),
            "destination":"files/fonts/msgothic.ttc","bundled_in_apk":False,
            "game_files_modified":False,"player_saves_modified":False,
            "restart_required":True,"evidence":str(folder.relative_to(ROOT))}
    write_json(folder/"report.json",report)
    write_json(ROOT/"reports/android_personal_font.json",report)
    print(json.dumps(report))
    return 0


if __name__=="__main__":raise SystemExit(main())
