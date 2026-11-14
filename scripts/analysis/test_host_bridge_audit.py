"""New host-action coverage and concrete cache/async ownership regressions."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from audit_player_semantics import registered_providers
from lua_bridge import table_value
from player_paths import player_path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,read_json,timestamp,write_json


def run():
    folder=archive_dir("host_bridge_audit");checks=[]
    java=(ROOT/"player/android/src/org/majiro/player/GameBridge.java").read_text(encoding="utf8")
    cpp=(ROOT/"player/android/game_host.cpp").read_text(encoding="utf8")
    service_actions=set(re.findall(r'action\.equals\("([^"]+)"\)',java))
    platform_actions=set(re.findall(r'action=="([^"]+)"',cpp))
    required_service={"font_quality","system_lcid","force_key_paste","open_url","open_help",
                      "clipboard_set","clipboard_get","clipboard_changed","checker_begin","checker_poll","checker_cancel"}
    required_platform={"caps_get","caps_set","cursor_move","display_scale","fullscreen_mode","service_events"}
    assert required_service<=service_actions,required_service-service_actions
    assert required_platform<=platform_actions,required_platform-platform_actions
    checks.append("every new utility/checker/clipboard/locale/display action has an actual Android bridge branch")
    source={"generation":0,"text":"\"first\",1\n\"tail\",2"}
    lua,_,_,system=registered_providers(overrides={
        "file_read":lambda name:source["text"],"file_write":lambda name,text:source.update(text=text)})
    providers=system["providers"];vm=lua.table()
    old=providers["$file_open"](vm,lua.table_from(["fixture.cfg"]))
    assert providers["$file_read_line_raw$"](vm,lua.table_from([old]))=='"first",1'
    # The options object is captured by the production file provider. Change
    # its source generation through the same real archive wrapper used in boot.
    boot=player_path("runtime/player_boot.lua").read_text(encoding="utf8")
    prefix=boot[:boot.index("if raw_patch then\n  assert(not text_patch")]
    host=lua.table()
    configuration=lua.table_from({"identity":"a"*64,"width":800,"height":600},recursive=True)
    lua.globals()["AUDIT_CONFIGURATION"]=configuration
    paths={"runtime/player_configuration.lua":"return {make=function()return AUDIT_CONFIGURATION end}",
           "runtime/majiro_save_compatibility.lua":"return {new=function()return {recover=function()return {recovered=0,rejected=0,conflicts=0}end,accepts=function()return false end}end}",
           "scripts/opcodes.lua":"return {}","scripts/syscall_names.lua":"return {}",
           "runtime/majiro_text_resources.lua":player_path("runtime/majiro_text_resources.lua").read_text(encoding="utf8"),
           "runtime/majiro_codec.lua":"return {}",
           "runtime/majiro_store.lua":"return {new=function()return {load_index=function()return {}end}end}"}
    host["asset_text"]=lambda path:paths.get(path)or player_path(path).read_text(encoding="utf8")
    host["game_text"]=lambda path:None
    text_events=[]
    host["text_mode"]=lambda:"original"
    host["text_resource_names"]=lambda:lua.table_from(["start.mjo"])
    def script(name):
        assert name=="start.mjo"
        lua.globals()["PLAYER_VERIFY_TEXT_RESOURCE"](name,"b"*64,"original",name)
        text_events.append(("script",name))
        return lua.table_from({"name":name})
    def storage(request):
        assert request.startswith("sha256\n"),"unexpected fixture storage operation"
        return"OK "+hashlib.sha256(request[7:].encode("utf8")).hexdigest()
    host["script"]=script
    host["storage"]=storage
    host["seal_text_resources"]=lambda:text_events.append(("sealed",))
    host["set_text_identity"]=lambda identity,encoding:text_events.append(("identity",identity,encoding))
    host["archive_control"]=lambda action,name:0
    host["member_names"]=lambda:lua.table_from(["new.cfg","new.rct"])
    host["image_metadata"]=lambda name:lua.table_from({"width":3,"height":7})
    lua.globals()["HOST"]=host
    options=lua.execute(prefix+"\nreturn options\n")
    expected_identity=hashlib.sha256(("verified-text-v2\n"+"a"*64+"\nvm:cp932").encode("utf8")).hexdigest()
    assert text_events==[("script","start.mjo"),("sealed",),("identity",expected_identity,"cp932")]
    assert options["source_identity"]==expected_identity
    checks.append("production boot preflights original text, seals resources and publishes verified identity before archive refresh")
    options["archive"]("register","update.arc")
    assert options["source_generation"]==1
    assert options["resource_names"]["new.cfg"]and options["image_metadata"]["new.rct"]["height"]==7
    # Exercise the production system directly with an archive generation
    # callback; both handles retain independent token/line snapshots.
    module=lua.execute(player_path("runtime/majiro_syscalls.lua").read_text(encoding="utf8"))
    actual_options=lua.table_from({"names":{},"config_files":{},"config_lines":{},
        "environment":{},"resource_names":{},"width":800,"height":600},recursive=True)
    actual_options["modules"]=lambda name:lua.execute(player_path(name).read_text(encoding="utf8"))
    actual_options["now"]=lambda:0
    actual_options["file_read"]=lambda name:source["text"]
    actual=module["new"](actual_options);p=actual["providers"]
    old=p["$file_open"](vm,lua.table_from(["fixture.cfg"]))
    assert p["$file_read_line_raw$"](vm,lua.table_from([old]))=='"first",1'
    source["text"]='"replacement",9\n"changed",8';actual_options["source_generation"]=1
    new=p["$file_open"](vm,lua.table_from(["fixture.cfg"]))
    assert p["$file_read_line_raw$"](vm,lua.table_from([new]))=='"replacement",9'
    assert p["$file_read_line_raw$"](vm,lua.table_from([old]))=='"tail",2'
    checks.append("dynamic archive refreshes metadata/catalogue and subsequent config opens while old handles retain original lines")
    fresh,_,_,s=registered_providers(overrides={"service":lambda action,value:"not-a-number"})
    ok,why=fresh.eval("function(fn)return pcall(fn,{}, {})end")(s["providers"]["$clipbord_is_change"])
    assert not ok and"invalid change state"in str(why)
    checks.append("invalid clipboard change-state response fails instead of being reported as a change")
    for terminate in("generation","finished","wait_changed"):
        calls=[]
        def service(action,value):
            calls.append(action);return"pending"if action=="checker_begin"else\
                "OK"if action=="checker_cancel"else"authenticated 9004"
        lua,_,_,s=registered_providers(overrides={"service":service})
        mod=lua.execute(player_path("runtime/majiro_vm.lua").read_text(encoding="utf8"))
        ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf8"))
        target=lua.eval("""function(mod,ops,system)
          local script={name='owner-fixture',main=0,functions={},code={{0,0x191},{8,0x191}}}
          local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=system.invoke}
          vm:begin(script.name);return vm
        end""")(mod,ops,s)
        s["providers"][0xf430015b](target,lua.table(),True)
        ok,why=lua.eval("function(vm)return pcall(vm.export_state,vm)end")(target)
        assert not ok and"external owner request"in str(why)
        if terminate=="generation":target["context_generation"]+=1
        elif terminate=="finished":target["threads"][1]["finished"]=True
        else:target["waiting"]=None
        ok,why=lua.eval("function(system,vm)return pcall(system.poll,vm,{})end")(s,target)
        assert not ok
        assert target["external_checker"]is None
        assert s["state"]["external_checker_authenticated"]is None
        if terminate!="wait_changed":assert"checker_cancel"in calls
    checks.append("checker rejects stale generation, finished owner and changed continuation without authenticating or reviving old work")
    attempts=[]
    lua,_,_,s=registered_providers(overrides={"service":lambda action,value:
        attempts.append(action)or"missing external authenticator"})
    s["state"]["external_checker_authenticated"]=True
    fake=lua.table_from({"thread_id":1})
    fake["fail"]=lua.eval("function(self,why)error(why)end")
    ok,why=lua.eval("function(fn,vm)return pcall(fn,vm,{})end")(s["providers"][0xf430015b],fake)
    assert not ok and attempts==["checker_begin"]
    checks.append("a saved diagnostic authentication flag cannot bypass a fresh external authentication attempt")
    for change in("generation","wait_changed"):
        actions=[];host={"response":"pending"}
        def dialog(action,*args):
            actions.append(action)
            return"OK"if action=="cancel"else host["response"]
        lua,_,_,s=registered_providers(overrides={"dialog":dialog})
        module=lua.execute(player_path("runtime/majiro_vm.lua").read_text(encoding="utf8"))
        ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf8"))
        target=lua.eval("""function(mod,ops,system)
          local script={name='dialog-owner-fixture',main=0,functions={},code={{0,0x191},{8,0x191}}}
          local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=system.invoke}
          vm:begin(script.name);return vm
        end""")(module,ops,s)
        s["providers"]["$dialog_ok"](target,lua.table_from(["fixture"]),True)
        if change=="generation":target["context_generation"]+=1
        else:target["waiting"]=None;host["response"]="result 1"
        ok,why=lua.eval("function(s,v)return pcall(s.poll,v,{})end")(s,target)
        assert not ok and not s["dialogs"]["active"]()and target["modal_dialog"]is None
        if change=="generation":assert"cancel"in actions
    checks.append("dialog completion checks its owner generation and continuation, clears modal state, and cancels obsolete host work")
    # Compile all modified Android boundary classes against the real SDK.
    tools=read_json(ROOT/"reports/android_toolchain.json")
    android=Path(tools["sdk"])/"platforms/android-36/android.jar"
    compiler=Path(tools["java"])/"bin/javac.exe"
    classes=folder/"classes";classes.mkdir()
    bridge_sources=list((ROOT/"player/android/src").rglob("*.java"))+list(
        (ROOT/"references/player/ijk/java").rglob("*.java"))
    process=subprocess.run([str(compiler),"-encoding","UTF-8","-source","8","-target","8",
        "-bootclasspath",os.pathsep.join((str(android),str(Path(tools["sdk"])/"build-tools/36.0.0/core-lambda-stubs.jar"))),
        "-classpath",str(ROOT/"references/player/SDL3/classes.jar"),
        "-d",str(classes),*map(str,bridge_sources)],capture_output=True)
    (folder/(folder.name[:8]+"_javac.txt")).write_bytes(process.stdout+process.stderr)
    if process.returncode:raise RuntimeError("host audit Android compilation failed: "+str(folder))
    checks.append("all production Android host classes compile against pinned Android API 36")
    clang=Path(tools["sdk"])/"ndk/28.2.13676358/toolchains/llvm/prebuilt/windows-x86_64/bin/clang++.exe"
    syntax=subprocess.run([str(clang),"--target=aarch64-linux-android26","-std=c++20",
        "-fsyntax-only","-DMAJIRO_DEBUG_BRIDGE","-I"+str(ROOT/"player/core"),
        "-I"+str(ROOT/"references/player/lua/src"),"-I"+str(ROOT/"references/player/SDL3/include"),
        str(ROOT/"player/android/game_host.cpp")],capture_output=True)
    (folder/(folder.name[:8]+"_clang_syntax.txt")).write_bytes(syntax.stdout+syntax.stderr)
    if syntax.returncode:raise RuntimeError("Android game host syntax failed: "+str(folder))
    checks.append("production JNI/SDL host passes ARM64 Android compiler syntax checking")
    report={"schema":1,"timestamp":timestamp(),"status":"passed","checks":checks,
        "service_actions":sorted(required_service),"platform_actions":sorted(required_platform),
        "device_executed":False,"scope":"new host boundaries and targeted regression fixtures",
        "remaining_risks":["external authentication companion and legacy Windows user-key import are unavailable",
                           "dynamic archive position/script-save mapping remains separate compatibility work",
                           "system locale LCID mapping uses explicit language/country cases and a custom-locale fallback"]}
    write_json(folder/(folder.name[:8]+"_host_bridge_audit.json"),report)
    write_json(ROOT/"reports/host_bridge_audit.json",report)
    print("host bridge audit:",len(checks),"coverage/cache/ownership/compile checks passed")


if __name__=="__main__":run()
