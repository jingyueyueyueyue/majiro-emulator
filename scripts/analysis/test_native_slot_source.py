"""Source-driven native picker confirmation using isolated real saves."""
from pathlib import Path
import argparse
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"scripts"/n)for n in("analysis","convert","build")]
from port_common import read_json,write_json,archive_dir,timestamp
from desktop_pixel_session import PixelSession
from test_desktop_game_batch import files_for,tick,settle
from test_story_entry_regressions import run_story
from run_background_compatibility import source_identity
JAVA_SOURCE=ROOT/"player/android/src/org/majiro/player"


class NativeSession(PixelSession):
    def __init__(self,*args,java,classes,**kwargs):
        self.java,self.classes=java,classes
        self.requests={};self.slot_request=None;self.actions=[];self.input_trace=[]
        super().__init__(*args,**kwargs)

    def dialog(self,action,token,*args):
        if action=="begin":
            flags,message,caption=args
            self.requests[token]={"flags":flags,"message":message,"caption":caption,"reply":"pending"}
            self.actions.append({"kind":"dialog_begin","token":token,"flags":flags,"message":message,
                "clock":self.clock,"script":self.vm["frame"](self.vm)["script"]["name"],
                "offset":self.vm["current_offset"]})
            if flags==327681:self.slot_request=token
            return"pending"
        if action=="cancel":
            self.requests.pop(token,None);return"OK"
        request=self.requests[token]
        if request["flags"]==327681:
            value=request["reply"]
            if value!="pending":
                self.requests.pop(token);self.slot_request=None
            return value
        self.requests.pop(token)
        if request["flags"]>=65536:return"data "
        buttons=request["flags"]%16
        # Explicit local fixture approvals exercise the source's own load,
        # overwrite and saved-message continuations. Player data is absent.
        return"result "+str(6 if buttons==4 else 4 if buttons==5 else 1)

    def reply(self,action,slot=""):
        request=self.requests[assert_value(self.slot_request,"native slot dialog was not opened")]
        data=json.loads(request["message"]);ids=[r["id"]for r in data["entries"]]
        r=subprocess.run([str(self.java/"java.exe"),"-cp",str(self.classes),
            "org.majiro.player.NativeSlotRoundtrip",str(data["loading"]).lower(),
            ",".join(map(str,ids)),request["message"],"",action,str(slot)],
            cwd=ROOT,capture_output=True,timeout=15)
        assert r.returncode==0,r.stderr
        request["reply"]=r.stdout.decode("utf8")
        self.input_trace.append({"kind":"native_selection","action":action,"slot":slot,
                                 "reply":request["reply"],"available":ids,"clock":self.clock})
        return request["reply"]


def assert_value(value,message):
    assert value is not None,message
    return value


def wait_for_story(session):
    for _ in range(300):
        tick(session)
        if session.slot_request:return"slot"
        state=session.system["state"]
        if not session.vm["modal_dialog"]and state["reading_wait"]and not any(
                frame["script"]["name"]in("loadsave.mjo","config.mjo")
                for frame in session.vm["frames"].values()):
            return"story"
    raise AssertionError("source did not return from native dialog")


def open_picker(session,command):
    tick(session,{"window_command":command,"buttons":0,"held_buttons":0})
    for _ in range(200):
        if session.slot_request:return
        tick(session)
    raise AssertionError("source window event did not open native picker")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages",type=int,default=12)
    args=parser.parse_args()
    folder=archive_dir("native_slot_source")
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    classes=folder/"classes"
    r=subprocess.run([str(java/"javac.exe"),"-encoding","UTF-8","-source","8","-target","8",
        "-d",str(classes),*[str(JAVA_SOURCE/n)for n in(
            "NativeSlotSelection.java","ModalDialogState.java")],str(ROOT/"scripts/analysis/NativeSlotRoundtrip.java")],
        cwd=ROOT,capture_output=True,timeout=45)
    (folder/"javac.txt").write_bytes(r.stdout+r.stderr);assert r.returncode==0
    game=next(g for g in read_json(ROOT/"config/recovered_library.json")["games"]if g["id"]=="happy_princess")
    before=source_identity();report={"timestamp":timestamp(),"status":"running","cases":[],
        "production_integrated":True,"device_accessed":False,"player_saves_used":False,
        "source_identity":before,"scope":"source native Happy window menu, real isolated saves, actual Java selection replies and native pixels",
        "evidence":str(folder.relative_to(ROOT))}
    for mode in("original","online"):
        session=NativeSession(game,files_for(game),mode,folder/mode,java=java,classes=classes)
        row={"mode":mode,"status":"running"}
        try:
            story=run_story(game,session.files,mode,args.pages,max(90,args.pages*.6),session=session)
            assert story["status"]=="passed",story
            settle(session,1000,4000);state=session.system["state"]
            text,generation=state["console"]["text"],state["reading_generation"]
            open_picker(session,1)
            assert session.reply("default")=="pending"
            for _ in range(3):tick(session)
            assert session.vm["status"]!="blocked"and session.slot_request
            assert session.reply("cancel")=="data "
            assert wait_for_story(session)=="story"
            open_picker(session,3);session.reply("choose",101)
            assert wait_for_story(session)=="story"
            assert state["save_catalog"]["slot_00101"] is not None
            row["saved_frame"]=session.capture("after_native_save")
            assert row["saved_frame"]["nonblack"],"source has no pixels after native save"
            for _ in range(30):
                if state["reading_generation"]>=generation+2:break
                tick(session,{"x":400,"y":300,"buttons":1,"held_buttons":1},500)
                tick(session,{"buttons":0,"held_buttons":0},50)
                settle(session,500,3000)
            assert state["reading_generation"]>=generation+2
            row["advanced_generation"]=state["reading_generation"]
            open_picker(session,1);session.reply("choose",101)
            assert wait_for_story(session)=="story"
            assert state["console"]["text"]==text and state["reading_generation"]==generation
            row["loaded_frame"]=session.capture("after_native_load")
            assert row["loaded_frame"]["nonblack"],"source has no pixels after native load"
            # Use actual production checkpoint serialization to generate
            # controlled recent records. This does not claim the source's
            # quick-save gesture was exercised.
            for slot in(10001,10003):
                session.system["providers"]["$do_save"](session.vm,session.lua.table_from([slot]))
                open_picker(session,1);session.reply("choose",slot)
                assert wait_for_story(session)=="story"
                assert state["console"]["text"]==text and state["reading_generation"]==generation
            row.update(status="passed",normal_save_load=True,empty_load_remained_pending=True,
                       quick_and_auto_fixture_snapshots_loaded=True,reading_generation=generation,
                       requested_story_pages=args.pages)
        except Exception as error:
            row.update(status="failed",error=str(error))
            if session.system:row["failure_frame"]=session.capture("failure")
        write_json(session.folder/"dialog_trace.json",session.actions)
        write_json(session.folder/"input_trace.json",session.input_trace)
        report["cases"].append(row)
        write_json(folder/"report.json",report)
        print(mode,row["status"],row.get("error",""),flush=True)
    report.update(status="passed"if all(r["status"]=="passed"for r in report["cases"])else"failed",
                  source_changed=before!=source_identity())
    write_json(folder/"report.json",report);write_json(ROOT/"reports/native_slot_source.json",report)
    print(folder)
    return int(report["status"]!="passed"or report["source_changed"])


if __name__=="__main__":raise SystemExit(main())
