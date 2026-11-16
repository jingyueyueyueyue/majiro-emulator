"""Batch cold-start tests on the recovery app, preserving the original app/saves."""
from pathlib import Path
import argparse
import atexit
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

PACKAGE="com.tyranor.runtime.majiro.recovery"
COMPONENT=PACKAGE+"/org.majiro.player.MainActivity"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial",default="ea615a82")
    parser.add_argument("--package",default=PACKAGE,
                        choices=("com.tyranor.runtime.majiro","com.tyranor.runtime.majiro.recovery"))
    parser.add_argument("--repeats",type=int,default=2)
    parser.add_argument("--seconds",type=int,default=90)
    parser.add_argument("--observe-seconds",type=float,default=20,
                        help="Keep observing after the first input boundary; catch delayed music/animation errors.")
    parser.add_argument("--pages",type=int,default=0,
                        help="Use source pointer/key input to enter the story and require this many reading pages.")
    parser.add_argument("--min-session-seconds",type=float,default=0,
                        help="Require a sustained session after reaching the requested reading pages.")
    parser.add_argument("--report",type=Path,
                        help="Write a focused run without replacing the whole-library report.")
    parser.add_argument("--game",action="append")
    parser.add_argument("--mode",action="append",choices=("current","original","local"))
    parser.add_argument("--resume-report",type=Path,help="Reuse passed cases only for the identical installed APK.")
    parser.add_argument("--profile-startup",action="store_true",
                        help="Record 16 seconds of CPU samples with the device's simpleperf.")
    args=parser.parse_args()
    package=args.package
    component=package+"/org.majiro.player.MainActivity"
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
    debug_socket=("majiro-debug-recovery"if package.endswith(".recovery")else"majiro-debug-"+package)
    command("forward","tcp:43217","localabstract:"+debug_socket)
    def debug(query):
        with socket.create_connection(("127.0.0.1",43217),timeout=2)as client:
            client.sendall((query+"\n").encode());data=bytearray()
            while not data.endswith(b"\n"):
                part=client.recv(65536)
                if not part:break
                data.extend(part)
        return json.loads(data)
    # An asleep/locked Activity does not run SDL ticks. Keep this reversible
    # test condition explicit rather than interpreting it as a game timeout.
    previous_awake=command("shell","settings","get","global","stay_on_while_plugged_in").decode().strip()
    write_json(folder/"device_state.json",{"previous_stay_on_while_plugged_in":previous_awake})
    def restore_awake():
        setting=["settings","delete","global","stay_on_while_plugged_in"]if previous_awake=="null"else[
            "settings","put","global","stay_on_while_plugged_in",previous_awake]
        subprocess.run([*base,"shell",shlex.join(setting)],capture_output=True,timeout=15)
    atexit.register(restore_awake)
    command("shell","svc","power","stayon","true")
    command("shell","input","keyevent","224")
    command("shell","wm","dismiss-keyguard")
    installed=command("shell","pm","path",package).decode("utf8").strip().splitlines()[0].removeprefix("package:")
    installed_sha256=command("shell","sha256sum",installed).decode("ascii").split()[0]
    build=read_json(ROOT/"reports/android_probe_build.json")
    if installed_sha256!=build["sha256"]:raise RuntimeError("tablet APK differs from the current build")
    command("shell","am","force-stop",package)
    namespace="test-"+folder.name
    generated_saves="files/diagnostic-saves/"+namespace
    guard={"original":"files/games","generated":generated_saves,"namespace":namespace,"status":"preparing"}
    def private(*args,check=True):
        result=subprocess.run([*base,"shell",shlex.join(["run-as",package,*args])],
                              capture_output=True,timeout=45)
        if check and result.returncode:raise RuntimeError(result.stderr.decode("utf8","replace"))
        return result
    # A test namespace is selected before GameBridge opens its save store.
    # No rename of the player's root is needed, including after interruption.
    private("mkdir","-p",generated_saves)
    guard["player_root_moved"]=False
    guard["status"]="isolated"
    write_json(folder/"save_isolation.json",guard)
    def restore_saves():
        if guard["status"]!="isolated":return
        command("shell","am","force-stop",package)
        guard["status"]="player_saves_restored"
        write_json(folder/"save_isolation.json",guard)
        archive=folder/"test_generated_saves.tar"
        with archive.open("wb")as output:
            result=subprocess.run([*base,"exec-out","run-as",package,"tar","-cf","-",generated_saves],
                                  stdout=output,stderr=subprocess.PIPE,timeout=45)
        if result.returncode:raise RuntimeError("test saves preserved on device; archive transfer failed")
        import tarfile
        with tarfile.open(archive)as saved_archive:
            assert all(member.name==generated_saves or member.name.startswith(generated_saves+"/")
                       for member in saved_archive.getmembers())
        # Exact tool-owned test directory only, after the real save root is back
        # and a readable local archive of generated test files is present.
        assert generated_saves.startswith("files/diagnostic-saves/test-2026")and".."not in generated_saves
        private("rm","-rf",generated_saves)
        guard["status"]="player_saves_restored_and_test_files_archived"
        write_json(folder/"save_isolation.json",guard)
    atexit.register(restore_saves)
    modes=args.mode or["current","original"]
    previous=read_json(args.resume_report)if args.resume_report else {}
    if previous:
        if previous.get("apk_sha256")!=installed_sha256:raise RuntimeError("resume APK differs")
        if previous.get("required_story_pages",0)<args.pages:raise RuntimeError("resume page target is weaker")
        if previous.get("observe_seconds",0)<args.observe_seconds:raise RuntimeError("resume observation is weaker")
        if previous.get("minimum_session_seconds",0)<args.min_session_seconds:raise RuntimeError("resume session is weaker")
    reused={(row["game"],row["mode"],row["repeat"]):row for row in previous.get("cases",[])
            if row.get("status")=="passed"}
    report={"status":"running","timestamp":timestamp(),"application_id":package,"cases":[],
            "apk_sha256":installed_sha256,"version_name":build["version_name"],"version_code":build["version_code"],
            "modes":modes,"expected_cases":len(games)*len(modes)*args.repeats,
            "observe_seconds":args.observe_seconds,"required_story_pages":args.pages,
            "minimum_session_seconds":args.min_session_seconds,
            "player_saves_isolated":True,"save_isolation_record":str((folder/"save_isolation.json").relative_to(ROOT)),
            "real_translation_api_used":False,"original_app_modified":False,
            "scope":"real device cold startup, delayed failures, optional source-input story pages and actual CPU/SDL pixels; no full route claim",
            "evidence":str(folder.relative_to(ROOT))}
    receipt=args.report or ROOT/"reports/android_library_batch.json"
    for repeat in range(args.repeats):
        for mode,game in product(modes,games):
            if (game["id"],mode,repeat)in reused:
                row=dict(reused[(game["id"],mode,repeat)],reused_from=str(args.resume_report))
                report["cases"].append(row)
                write_json(receipt,report);write_json(folder/"report.json",report)
                print(json.dumps({"game":game["id"],"mode":mode,"status":"passed","reused":True}),flush=True)
                continue
            began=time.monotonic()
            row={"game":game["id"],"repeat":repeat,"mode":mode,"status":"running"}
            safe=re.sub("[^a-zA-Z0-9_-]","_",game["id"])+"_"+mode+"_"+str(repeat)
            try:
                command("shell","am","force-stop",package)
                log_since=command("shell","date","+%m-%d %H:%M:%S.000").decode("ascii").strip()
                root="primary:Download/MajiroGames/"+game["device_directory"]
                launch=["shell","am","start","-W","-n",component,"--es","diagnostic_root",root]
                launch+=["--es","diagnostic_save_namespace",namespace]
                if mode!="current":launch+=["--es","diagnostic_mode",mode]
                output=command(*launch)
                (folder/(safe+"_start.txt")).write_bytes(output)
                last=None;status=None;render=None;stable=0;last_log=0;last_memory=0;seen_pid=None
                profiler=None;profile_log=None
                device_perf="/data/local/tmp/majiro-"+folder.name+"-"+safe+".perf"
                row["memory_samples"]=[]
                row["timeline"]=[]
                first_boundary=None;first_read=None;last_action=0;selected={};actions=[]
                row["actions"]=actions;row["story_pages"]=0
                while time.monotonic()-began<args.seconds:
                    if time.monotonic()-last_log>=2:
                        last_log=time.monotonic()
                        process=subprocess.run([*base,"shell","pidof",package+":game"],
                                               capture_output=True,timeout=5)
                        pid=process.stdout.decode("ascii").strip()
                        if seen_pid and pid!=seen_pid:
                            raise RuntimeError("game process exited or restarted during sustained playback")
                        if pid:
                            seen_pid=pid
                            if args.profile_startup and profiler is None:
                                profile_log=(folder/(safe+"_simpleperf.txt")).open("wb")
                                profiler=subprocess.Popen([*base,"shell",shlex.join([
                                    "/system/bin/simpleperf","record","--app",package,"-e","cpu-clock",
                                    "-f","997","--call-graph","fp","-p",pid,"--duration","16",
                                    "-o",device_perf])],
                                    stdout=profile_log,stderr=subprocess.STDOUT)
                            if time.monotonic()-last_memory>=5:
                                last_memory=time.monotonic()
                                raw=command("shell","run-as",package,"cat","/proc/"+pid+"/status").decode("ascii")
                                memory={"seconds":round(time.monotonic()-began,2),"pid":int(pid)}
                                for key in ("VmRSS","VmSize","VmData","VmSwap","Threads"):
                                    match=re.search(r"^"+key+r":\s+(\d+)",raw,re.M)
                                    if match:memory[key]=int(match[1])
                                maps=command("shell","run-as",package,"wc","-l","/proc/"+pid+"/maps")
                                memory["map_count"]=int(maps.split()[0])
                                row["memory_samples"].append(memory)
                                write_json(folder/(safe+"_memory.json"),row["memory_samples"])
                            logs=command("logcat","-d","-T",log_since,"--pid="+pid,"-v","brief","-s",
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
                        marker=[frame,bool(status.get("ui_waiting")),bool(status.get("reading_wait")),
                                status.get("reading_generation",0)]
                        if not row["timeline"] or row["timeline"][-1]["marker"]!=marker:
                            row["timeline"].append({"seconds":round(time.monotonic()-began,3),
                                "marker":marker,"steps":status.get("steps"),"vm_us":render.get("vm_us"),
                                "image_misses":render.get("image_cache_misses")})
                        if at_wait and available:
                            row.setdefault("first_input_seconds",round(time.monotonic()-began,3))
                        if status.get("reading_wait") and available:
                            row.setdefault("first_reading_seconds",round(time.monotonic()-began,3))
                        if at_wait and available:
                            stable=stable+1 if frame==last else 1
                            if stable>=4 and first_boundary is None:first_boundary=time.monotonic()
                            generation=status.get("reading_generation",0)
                            if status.get("reading_wait"):
                                if first_read is None:first_read=generation
                                row["story_pages"]=max(row["story_pages"],generation-first_read+1)
                            if (stable>=4 and first_boundary is not None and
                                time.monotonic()-first_boundary>=args.observe_seconds and
                                time.monotonic()-began>=args.min_session_seconds and
                                row["story_pages"]>=args.pages and render.get("presented",0)>0):
                                row["status"]="passed";break
                            if args.pages and row["story_pages"]<args.pages and time.monotonic()-last_action>=1.5:
                                if status.get("reading_wait"):
                                    width=pixels.get("width",800);height=pixels.get("height",600)
                                    # Source console buttons can occupy the bottom
                                    # of the screen. Pick an interior point outside
                                    # every active source area before clicking.
                                    regions=status.get("areas")or[]
                                    regions=regions if isinstance(regions,list)else[]
                                    x,y=width*.5,height*.35
                                    for fx,fy in ((.5,.35),(.5,.5),(.3,.3),(.7,.3),(.5,.65),(.2,.6)):
                                        candidate=(width*fx,height*fy)
                                        if not any(a.get("width",0)>0 and a.get("height",0)>0 and
                                                   a["x"]<=candidate[0]<a["x"]+a["width"]and
                                                   a["y"]<=candidate[1]<a["y"]+a["height"]for a in regions):
                                            x,y=candidate;break
                                    debug(f"MOVE {x:g} {y:g}");time.sleep(.08);debug(f"CLICK {x:g} {y:g} 1")
                                    actions.append({"kind":"story_click","generation":generation})
                                    last_action=time.monotonic()
                                elif stable>=4:
                                    active=(status.get("active_ui_script")or"").lower()
                                    if first_read is not None and any(name in frame.lower()for name in(
                                        "loadsave.mjo","config.mjo","history.mjo","cgmode.mjo","music.mjo")):
                                        debug("CLICK 400 300 2")
                                        actions.append({"kind":"return_from_menu","frames":frame})
                                        last_action=time.monotonic();last=frame;continue
                                    areas=status.get("areas")or[]
                                    areas=[a for a in areas if isinstance(a,dict)and
                                           a.get("id",0)>0 and a.get("width",0)>0 and a.get("height",0)>0]
                                    areas.sort(key=lambda a:(a["y"],a["x"]))
                                    signature=frame+json.dumps([(a["id"],a["x"],a["y"])for a in areas])
                                    attempt=selected.get(signature,0)
                                    if attempt<len(areas):
                                        area=areas[attempt];selected[signature]=attempt+1
                                        x=area["x"]+area["width"]/2;y=area["y"]+area["height"]/2
                                        debug(f"MOVE {x:g} {y:g}");time.sleep(.08);debug(f"CLICK {x:g} {y:g} 1")
                                        actions.append({"kind":"source_menu","area":area,"frames":frame,
                                                        "seconds":round(time.monotonic()-began,3)})
                                        last_action=time.monotonic()
                        else:
                            stable=0
                        last=frame
                    except (OSError,ValueError,ConnectionError):
                        pass
                    time.sleep(.5)
                row["status"]="passed"if row["status"]=="passed"else"timeout"
                row["observed_after_first_boundary_seconds"]=round(time.monotonic()-first_boundary,2)if first_boundary is not None else 0
                row["vm"]=status;row["render"]=render
                logs=command("logcat","-d","-T",log_since,"--pid="+seen_pid,"-v","brief","-s",
                             "MajiroPlayer:V").decode("utf8","replace")if seen_pid else ""
                phases={}
                for name,elapsed in re.findall(r"startup phase=(\w+) elapsed_ms=(\d+)",logs):
                    phases.setdefault(name,int(elapsed))
                row["startup_phases_ms"]=phases
                if status:
                    roots=private("find",generated_saves,"-type","d").stdout.decode("utf8","replace")
                    if "/games/" not in roots:
                        raise RuntimeError("diagnostic save namespace did not reach GameBridge")
                image=command("exec-out","screencap","-p")
                (folder/(safe+".png")).write_bytes(image)
                row["screenshot"]=str((folder/(safe+".png")).relative_to(ROOT))
                if profiler is not None:
                    profiler.wait(timeout=25);profile_log.close()
                    if profiler.returncode:raise RuntimeError("startup CPU sampling failed")
                    perf=folder/(safe+".perf")
                    command("pull",device_perf,str(perf))
                    command("shell","rm","-f",device_perf)
                    row["cpu_profile"]=str(perf.relative_to(ROOT))
                # UI waits alone do not validate layering; retain screenshots
                # and per-frame statistics for the final visual review.
            except Exception as error:
                if locals().get("profiler") is not None:
                    profiler.wait(timeout=25)
                    if profile_log:profile_log.close()
                row.update(status="failed",error=str(error))
                try:
                    (folder/(safe+"_failed.png")).write_bytes(command("exec-out","screencap","-p"))
                    row["screenshot"]=str((folder/(safe+"_failed.png")).relative_to(ROOT))
                except Exception:
                    pass
            row["seconds"]=round(time.monotonic()-began,2)
            report["cases"].append(row)
            write_json(receipt,report);write_json(folder/"report.json",report)
            print(json.dumps({k:row.get(k)for k in("game","mode","repeat","status","seconds","error")},ensure_ascii=True),flush=True)
    report.update(status="complete",timestamp=timestamp())
    report["failures"]=[{"game":r["game"],"mode":r["mode"],"repeat":r["repeat"],"status":r["status"],
                         "error":r.get("error")}
                        for r in report["cases"]if r["status"]!="passed"]
    report["status"]="passed"if not report["failures"]else"failed"
    restore_saves()
    report["player_saves_restored"]=guard["status"]=="player_saves_restored_and_test_files_archived"
    write_json(receipt,report);write_json(folder/"report.json",report)
    return int(bool(report["failures"]))


if __name__=="__main__":raise SystemExit(main())
