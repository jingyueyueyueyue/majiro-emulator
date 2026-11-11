"""Minimal portable external checkpoint export/import/cancel/validation checks."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json
from lua_bridge import create_lua
from player_paths import player_path


def main():
    lua=create_lua();clock={"now":0};external={"document":None,"cancel":False};graphics={"staged":False}
    def module(name):return lua.execute(player_path(name).read_text(encoding="utf8"))
    def transport(action,data):
        if external["cancel"]:return"CANCEL"
        if action=="save":external["document"]=data;return"OK"
        return"OK\n"+external["document"]
    def pixels(request):
        if request=="checkpoint_begin":graphics["staged"]=True;return"OK"
        if request in ("checkpoint_commit","checkpoint_rollback"):graphics["staged"]=False;return"OK"
        if request=="reset_pages":return"OK"
        if request.startswith("batch\n"):
            return"ERR invalid command"if"invalidpixel"in request else"OK"
        raise ValueError("unexpected graphics request")
    options=lua.table_from({"names":module("resources/scripts/syscall_names.lua"),
        "config_files":{},"config_lines":{},"environment":{},"resource_names":{},
        "source_identity":"a"*64,"width":1280,"height":720},recursive=True)
    options["modules"]=module;options["now"]=lambda:clock["now"]
    options["set_clock"]=lambda value:clock.update(now=value)
    options["external_save"]=transport;options["graphics"]=pixels;options["audio"]=lambda *args:True
    options["cp932"]=lambda text:text.encode("cp932")
    system=module("runtime/majiro_syscalls.lua")["new"](options)
    vm=lua.eval("""
      function(mod,ops,system)
        local script={name='external-fixture.mjo',source_sha256=string.rep('b',64),
          main=0,functions={},code={{0,0x82c,0}}}
        local vm=mod.new{opcodes=ops,loader=function()return script end,
          syscall=system.invoke,resume_action=system.resume_action,string_ops=system.strings}
        vm:begin(script.name);return vm
      end
    """)(module("runtime/majiro_vm.lua"),module("resources/scripts/opcodes.lua"),system)
    def call(name,*args):return system["providers"][name](vm,lua.table_from(args))
    codec=module("runtime/majiro_codec.lua")
    vm["globals"][1][123]=456
    call("$save_point");assert call("$do_extsave")==0
    original=external["document"]
    vm["globals"][1][123]=999
    call("$do_extload")
    assert vm["globals"][1][123]==456 and not graphics["staged"]
    checks=["external export/import retains checkpoint and source identity"]
    external["cancel"]=True
    assert call("$do_extsave")==-1 and call("$do_extload")==-1
    assert vm["globals"][1][123]==456
    external["cancel"]=False
    checks.append("both picker cancellations return -1 without mutation")
    for kind in ("identity","script","graphics","native"):
        packet=codec["decode"](original)
        if kind=="identity":packet["identity"]="c"*64
        elif kind=="script":packet["packet"]["vm"]["scripts"][1]["sha256"]="c"*64
        elif kind=="graphics":packet["graphics"]=lua.table_from(["invalidpixel 1"])
        external["document"]=("MajiroSavV1.105\0"if kind=="native"else codec["encode"](packet))
        vm["globals"][1][123]=777
        failed=False
        try:call("$do_extload")
        except Exception:failed=True
        assert failed and vm["globals"][1][123]==777 and not graphics["staged"],kind
        checks.append(kind+" rejection leaves current VM and pages unchanged")
    report={"schema":1,"timestamp":timestamp(),"status":"passed","checks":checks,
            "runtime_root":"player/runtime","native_story_import_supported":False,
            "android_document_picker_executed":False}
    folder=archive_dir("external_save_tests")
    write_json(ROOT/"reports/external_save_tests.json",report)
    write_json(folder/"20261110_external_save_tests.json",report)
    print("external saves:",len(checks),"minimal checkpoint/cancel/validation checks passed")


if __name__=="__main__":main()
