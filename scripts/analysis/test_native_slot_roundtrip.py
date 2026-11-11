"""Java state -> production Lua/save-key boundary; never mutates a game."""
from pathlib import Path
import argparse
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"scripts"/name)for name in("analysis","convert","build")]
from port_common import read_json,write_json,archive_dir,timestamp
from run_background_compatibility import source_identity
from lua_bridge import create_lua

JAVA_SOURCE=ROOT/"player/android/src/org/majiro/player"


def main():
    folder=archive_dir("native_slot_roundtrip")
    before=source_identity()
    java=Path(read_json(ROOT/"reports/android_toolchain.json")["java"])/"bin"
    classes=folder/"classes"
    files=[JAVA_SOURCE/name for name in("ModalDialogState.java","NativeSlotSelection.java")]
    files.append(ROOT/"scripts/analysis/NativeSlotRoundtrip.java")
    result=subprocess.run([str(java/"javac.exe"),"-encoding","UTF-8","-source","8","-target","8",
                           "-d",str(classes),*map(str,files)],cwd=ROOT,capture_output=True,timeout=45)
    (folder/"javac.txt").write_bytes(result.stdout+result.stderr)
    assert result.returncode==0
    cases=[]
    strict=[]
    definitions=[
        ("empty_load_default",True,{},"","default","",None),
        ("another_slot_default",True,{"slot_00205":{}},"","default","",None),
        ("existing_slot_explicit",True,{"slot_00205":{}},"","choose","205",205),
        ("existing101_not_preselected",True,{"slot_00101":{}},"","default","",None),
        ("quick_load",True,{"quick":{}},"","choose","10001",10001),
        ("auto_load",True,{"history_3":{}},"","choose","10003",10003),
        ("auto_oldest_load",True,{"history_4":{}},"","choose","10010",10010),
        ("missing_load",True,{},"","choose","101",None),
        ("zero_load",True,{},"","choose","0",None),
        ("out_of_range_load",True,{},"","choose","1101",None),
        ("save_default",False,{},"","default","",None),
        ("save101",False,{},"","choose","101",101),
        ("save1100",False,{},"","choose","1100",1100),
        ("save_quick_rejected",False,{"quick":{}},"","choose","10001",None),
        ("save_legacy_zero",False,{},"","choose","0",None),
        ("save_out_of_range",False,{},"","choose","1101",None),
        ("cancel_load",True,{},"","cancel","",-1),
        ("cancel_save",False,{},"","cancel","",-1),
        ("restore_selection",True,{"slot_00205":{}},"slot=205","restore","",205),
        ("restore_absent_selection",True,{},"slot=205","restore","",None),
        ("restore_malformed_selection",True,{},"slot=205\nslot=0","restore","",None),
        ("metadata_literal",True,{"slot_00205":{"info":"literal=1\nslot=0\n\u4eba\u540d","time":"test"}},"","choose","205",205),
    ]
    for prefix in ("player/runtime","runtime"):
        for name,loading,catalog,draft,action,selected,wanted in definitions:
            lua=create_lua()
            host_request={}
            response={"value":"pending"}

            def dialog(kind,token,flags,message,caption):
                if kind=="begin":
                    host_request.update(flags=flags,message=message,caption=caption)
                    return"pending"
                if kind=="poll":return response["value"]
                if kind=="cancel":return"OK"
                raise AssertionError(kind)

            def module(name):
                path=(ROOT/prefix/Path(name).name if name.startswith("runtime/")
                      else ROOT/"player/tables"/Path(name).name)
                return lua.execute(path.read_text("utf8"))

            options=lua.table_from({"names":{},"width":1280,"height":720,"config_files":{},
                "config_lines":{},"environment":{},"resource_names":{},"image_metadata":{},
                "movies":{},"source_identity":"slot-fixture"},recursive=True)
            options["modules"]=module;options["now"]=lambda:0;options["dialog"]=dialog
            syscalls=(ROOT/prefix/"majiro_syscalls.lua").read_text("utf8")
            system=lua.execute(syscalls)["new"](options)
            system["state"]["registry"]["quicknum"]=3
            for key,value in catalog.items():
                system["state"]["save_catalog"][key]=lua.table_from(
                    {"info":value.get("info","record"),"time":value.get("time","test")})
            vm=lua.eval("""function(mod,ops,system)
              local script={name='native-slot-roundtrip.mjo',main=0,functions={},
                code={{0,0x191},{2,0x82c,0}}}
              local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=system.invoke}
              vm:begin(script.name);return vm
            end""")(module("runtime/majiro_vm.lua"),module("scripts/opcodes.lua"),system)
            system["providers"][0x9af10167](vm,lua.table_from([int(loading),101]),True)
            descriptor=json.loads(host_request["message"])
            assert descriptor["schema"]==1 and descriptor["loading"]==loading
            assert descriptor["normal_min"]==101 and descriptor["normal_max"]==1100
            if name=="metadata_literal":
                assert descriptor["entries"][0]["info"]==catalog["slot_00205"]["info"]
            ids=[entry["id"]for entry in descriptor["entries"]]
            result=subprocess.run([str(java/"java.exe"),"-cp",str(classes),
                "org.majiro.player.NativeSlotRoundtrip",str(loading).lower(),",".join(map(str,ids)),
                host_request["message"],draft,action,selected],cwd=ROOT,capture_output=True,timeout=15)
            assert result.returncode==0,(name,result.stderr)
            response["value"]=result.stdout.decode("utf8")
            system["dialogs"]["poll"](vm)
            if wanted is None:
                assert response["value"]=="pending" and vm["modal_dialog"] is not None
                assert len(vm["stack"])==0 and vm["status"]!="blocked"
                # Explicit cancel exits the source dialog; no success is invented.
                response["value"]="data "
                system["dialogs"]["poll"](vm)
                assert vm["stack"][1]==-1
            else:
                assert vm["stack"][1]==wanted and vm["modal_dialog"] is None
            cases.append({"runtime":prefix,"case":name,"status":"passed","expected":wanted,
                          "java_reply":result.stdout.decode("utf8")})
            if name=="existing_slot_explicit":
                picker=module("runtime/majiro_slot_picker.lua")["new"](
                    system["state"],system["saves"]["key"],module("runtime/majiro_json.lua"))
                request=picker["request"](True,101)
                check=lua.eval("function(fn,request,payload)return pcall(fn,request,payload)end")
                for forged in("slot=0","slot=1101","slot=10002","slot=204",
                               "slot=205\nslot=0","not a selection"):
                    ok,why=check(picker["answer"],request,forged)
                    assert not ok,("unoffered/malformed host reply accepted",forged)
                    strict.append({"runtime":prefix,"payload":forged,"rejected":True})
    report={"timestamp":timestamp(),"status":"passed","cases":cases,"strict_host_reply_checks":strict,
            "source_identity":before,
            "source_changed":source_identity()!=before,"production_integrated":True,
            "device_accessed":False,"player_saves_used":False,
            "scope":"actual Java selection/reply plus actual Lua dialog providers and canonical save keys; Android view interaction is separate",
            "evidence":str(folder.relative_to(ROOT))}
    assert not report["source_changed"]
    write_json(folder/"report.json",report);write_json(ROOT/"reports/native_slot_roundtrip.json",report)
    print(folder);print(len(cases),"production Java/Lua/save-key roundtrips passed")


if __name__=="__main__":main()
