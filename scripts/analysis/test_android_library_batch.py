"""Batch cold-start tests on the recovery app, preserving the original app/saves."""
from pathlib import Path
import argparse
import json
from itertools import product
import re
import socket
import shlex
import subprocess
import sys
import time
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"convert")]
from port_common import ROOT,archive_dir,read_json,write_json,timestamp

PACKAGE="org.majiro.player.recovery"
COMPONENT=PACKAGE+"/org.majiro.player.MainActivity"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial",default="ea615a82")
    parser.add_argument("--repeats",type=int,default=2)
    parser.add_argument("--seconds",type=int,default=90)
    parser.add_argument("--game",action="append")
    parser.add_argument("--mode",action="append",choices=("current","original","local"))
    args=parser.parse_args()
    adb=Path(read_json(ROOT/"reports/android_toolchain.json")["sdk"])/"platform-tools/adb.exe"
    base=[str(adb),"-s",args.serial]
    folder=archive_dir("android_library_batch")
    games=read_json(ROOT/"config/recovered_library.json")["games"]
    games=[g for g in games if g.get("enabled",True)and(not args.game or g["id"]in args.game)]
    def command(*arguments,timeout=30):
        if arguments and arguments[0]=="shell":
            arguments=("shell",shlex.join(arguments[1:]))
        p=subprocess.run([*base,*arguments],capture_output=True,timeout=timeout)
        if p.returncode:raise RuntimeError(p.stderr.decode("utf8","replace"))
        return p.stdout
    command("forward","tcp:43217","localabstract:majiro-debug-recovery")
    def debug(query):
        with socket.create_connection(("127.0.0.1",43217),timeout=2)as client:
            client.sendall((query+"\n").encode());data=bytearray()
            while not data.endswith(b"\n"):
                part=client.recv(65536)
                if not part:break
                data.extend(part)
        return json.loads(data)
    installed=command("shell","pm","path",PACKAGE).decode("utf8").strip().splitlines()[0].removeprefix("package:")
    installed_sha256=command("shell","sha256sum",installed).decode("ascii").split()[0]
    build=read_json(ROOT/"reports/android_probe_build.json")
    if installed_sha256!=build["sha256"]:raise RuntimeError("tablet APK differs from the current build")
    modes=args.mode or["current","original"]
    report={"status":"running","timestamp":timestamp(),"application_id":PACKAGE,"cases":[],
            "apk_sha256":installed_sha256,"version_name":build["version_name"],"version_code":build["version_code"],
            "modes":modes,"expected_cases":len(games)*len(modes)*args.repeats,
            "real_translation_api_used":False,"original_app_modified":False,
            "scope":"real device cold startup, source wait and CPU/SDL frame statistics; no full route claim",
            "evidence":str(folder.relative_to(ROOT))}
    receipt=ROOT/"reports/android_library_batch.json"
    for repeat in range(args.repeats):
        for mode,game in product(modes,games):
            began=time.monotonic()
            row={"game":game["id"],"repeat":repeat,"mode":mode,"status":"running"}
            safe=re.sub("[^a-zA-Z0-9_-]","_",game["id"])+"_"+mode+"_"+str(repeat)
            try:
                command("shell","am","force-stop",PACKAGE)
                root="primary:Download/MajiroGames/"+game["device_directory"]
                launch=["shell","am","start","-W","-n",COMPONENT,"--es","diagnostic_root",root]
                if mode!="current":launch+=["--es","diagnostic_mode",mode]
                output=command(*launch)
                (folder/(safe+"_start.txt")).write_bytes(output)
                last=None;status=None;render=None;stable=0;last_log=0
                while time.monotonic()-began<args.seconds:
                    if time.monotonic()-last_log>=2:
                        last_log=time.monotonic()
                        process=subprocess.run([*base,"shell","pidof",PACKAGE+":game"],
                                               capture_output=True,timeout=5)
                        pid=process.stdout.decode("ascii").strip()
                        if pid:
                            logs=command("logcat","-d","--pid="+pid,"-v","brief","-s",
                                         "MajiroPlayer:V","AndroidRuntime:E").decode("utf8","replace")
                            (folder/(safe+"_runtime.txt")).write_text(logs,encoding="utf8")
                            errors=[line for line in logs.splitlines()if
                                    re.match(r"E/(MajiroPlayer|AndroidRuntime)",line)or"ERROR: "in line or
                                    "text-resources: "in line and"Exception"in line]
                            if errors:raise RuntimeError(errors[-1])
                    try:
                        status=debug("STATUS");render=debug("RENDER_STATS")
                        if status.get("problem"):raise RuntimeError("VM problem: "+json.dumps(status["problem"],ensure_ascii=True))
                        pixels=render.get("screen_pixels",{})
                        available=pixels.get("available",False)and pixels.get("visible_nonblack_pixels",0)>0
                        frame=json.dumps([f["script"]for f in status.get("frames",[])],sort_keys=True)
                        at_wait=status.get("ui_waiting")or status.get("reading_wait")
                        if at_wait and available:
                            stable=stable+1 if frame==last else 1
                            if stable>=4 and time.monotonic()-began>=4 and render.get("presented",0)>0:
                                row["status"]="passed";break
                        last=frame
                    except (OSError,ValueError,ConnectionError):
                        pass
                    time.sleep(.5)
                row["status"]="passed"if row["status"]=="passed"else"timeout"
                row["vm"]=status;row["render"]=render
                image=command("exec-out","screencap","-p")
                (folder/(safe+".png")).write_bytes(image)
                row["screenshot"]=str((folder/(safe+".png")).relative_to(ROOT))
                # UI waits alone do not validate layering; retain screenshots
                # and per-frame statistics for the final visual review.
            except Exception as error:
                row.update(status="failed",error=str(error))
            row["seconds"]=round(time.monotonic()-began,2)
            report["cases"].append(row)
            write_json(receipt,report);write_json(folder/"report.json",report)
            print(json.dumps({k:row.get(k)for k in("game","mode","repeat","status","seconds","error")},ensure_ascii=True),flush=True)
    report.update(status="complete",timestamp=timestamp())
    report["failures"]=[{"game":r["game"],"mode":r["mode"],"repeat":r["repeat"],"status":r["status"],
                         "error":r.get("error")}
                        for r in report["cases"]if r["status"]!="passed"]
    report["status"]="passed"if not report["failures"]else"failed"
    write_json(receipt,report);write_json(folder/"report.json",report)
    return int(bool(report["failures"]))


if __name__=="__main__":raise SystemExit(main())
