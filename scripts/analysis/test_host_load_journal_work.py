"""Measure load journal work while preserving validation of every submitted command."""
from pathlib import Path
import json
import sys
import time

from test_emulator_host_restore import fixture
from test_host_load_atomicity import captured_options
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,sha256,write_json


def main():
    lua,module,system,vm,store,transport,script=fixture()
    options=captured_options(lua,system["providers"]["$do_extload"])
    original_modules=options["modules"]
    compact=module("runtime/majiro_graphics_checkpoint.lua")
    count={"compactions":0,"graphics":0,"loads":0}
    def counted(commands,pages):
        count["compactions"]+=1
        return compact["compact"](commands,pages)
    # Reinstall production save provider with only its dependency instrumented.
    options["modules"]=lambda name:lua.table_from({"compact":counted})if name=="runtime/majiro_graphics_checkpoint.lua"else original_modules(name)
    definitions={}
    options["graphics"]=lambda request:count.update(graphics=count["graphics"]+1)or"OK"
    saves=module("runtime/majiro_save.lua")["install"](
        lambda name,fn:definitions.__setitem__(name,fn),system["state"],options,
        system["providers"],lambda vm,request:options["graphics"](request))
    state=system["state"]
    state["pages"][55]=lua.table_from({"width":1,"height":1,"depth":32})
    state["graphics_journal"]=lua.table_from(["create 55 1 1 0 32","fill 55 0 0 1 1 255"])
    definitions["$save_point"](vm,lua.table());definitions["$do_extsave"](vm,lua.table())
    codec=module("runtime/majiro_codec.lua")
    document=codec["decode"](transport["document"])
    document["graphics"]=lua.table_from(["create 55 1 1 0 32"]+
        ["fill 55 0 0 1 1 "+str(i%256)for i in range(2000)])
    transport["document"]=codec["encode"](document)
    count["compactions"]=0
    before=transport["loads"]
    started=time.perf_counter()
    definitions["$do_extload"](vm,lua.table())
    elapsed=time.perf_counter()-started
    compactions=count["compactions"];loads=transport["loads"]-before
    assert compactions==1,count
    assert loads==1,"saved script was loaded twice"
    assert len(state["graphics_journal"])==2
    # The full input, including obsolete operations, is staged before commit.
    rejected={"hit":False}
    def graphics(request):
        if "invalidpixel"in request:
            rejected["hit"]=True;return"ERR invalid"
        return"OK"
    options["graphics"]=graphics
    document["graphics"]=lua.table_from(["fill 55 invalidpixel"])
    transport["document"]=codec["encode"](document)
    ok,why=lua.eval("function(f,v)return pcall(f,v,{})end")(definitions["$do_extload"],vm)
    assert not ok and rejected["hit"],"compaction bypassed raw command validation"
    report={"status":"passed","timestamp":timestamp(),"commands":2001,
        "journal_compactions_per_load":compactions,
        "script_loader_calls_per_load":loads,
        "retained_commands":2,"elapsed_seconds":elapsed,
        "raw_invalid_command_rejected":True,"device_accessed":False,
        "source_sha256":sha256(ROOT/"player/runtime/majiro_save.lua"),
        "scope":"count production load dependency calls, not a device performance benchmark"}
    folder=archive_dir("host_load_journal_work")
    write_json(folder/"20261111_journal_work.json",report)
    write_json(ROOT/"reports/host_load_journal_work_tests.json",report)
    print(json.dumps(report))
    return report


if __name__=="__main__":
    main()
