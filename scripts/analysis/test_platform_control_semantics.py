"""Compare the shared platform providers with original EXE control families."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers,source_identity
from lua_bridge import table_value
from original_platform_controls import PlatformOracle
from original_screen import ScreenOracle
from test_platform_control_recovery import cases

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json

FIXTURE="""
function(mod,ops,system)
  local script={name='platform-fixture',main=0,functions={},code={{0,0x191},{8,0x191}}}
  local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=system.invoke}
  vm:begin(script.name)
  local function hex(value)
    return(value:gsub('.',function(c)return string.format('%02x',c:byte())end))
  end
  return{vm=vm,call=function(hash,args,discard)
    vm:frame().index=1;script.code[1]={0,discard and 0x835 or 0x834,hash,#args}
    for i=#args,1,-1 do vm:push(args[i])end
    vm:step()
    local values={}
    for i,v in ipairs(vm.stack)do values[i]=type(v)=='string'and hex(v)or v end
    while #vm.stack>0 do vm:pop()end
    return values
  end}
end
"""


def run():
    before=source_identity()
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    entries={s["sample"]:s["entries"]for s in
        json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]}
    report={"schema":1,"timestamp":timestamp(),"source_identity":before,"samples":[],"failures":[],"local_checks":[],
        "scope":"actual provider dispatch and result disposal, raw drop filename bytes, keyboard sampling suppression, "
                "caps request guard, emulated client bounds and physical cursor move callbacks",
        "host_boundaries":["fixture rectangle/cursor/caps state","recorded Win32 side effects",
                           "source-load-independent initialized original CRT"],
        "limits":["Android caps/cursor host adapter is a separate integration target",
                  "Android drag delivery must supply original filename bytes, not decoded Unicode text",
                  "OS keyboard hook/hotkey fan-out remains a host boundary"],
        "complete_engine_semantics":False}
    for sample in samples:
        oracle=PlatformOracle(sample,entries[sample["sample"]]);profile=oracle.profile()
        screen_profile=ScreenOracle.profile(oracle)
        counts,failed,arities=Counter(),Counter(),{}
        for hash_,args,fixture in cases(oracle):
            for discard in(False,True):
                counts[hash_]+=1;arities.setdefault(hash_,set()).add(len(args));reason=None
                actual_calls=[]
                def platform(action,values):
                    params=list(values.values())
                    actual_calls.append({"action":action,"args":params})
                    if action=="caps_get":return fixture.get("caps",0)
                    return 0
                lua,_,_,system=registered_providers(overrides={"platform_controls":profile,
                    "screen_controls":screen_profile,"platform":platform})
                mod=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf8"))
                ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf8"))
                machine=lua.eval(FIXTURE)(mod,ops,system);vm=machine["vm"]
                p=system["state"]["platform"];settings=system["state"]["settings"]
                size=fixture.get("size",(800,600));point=fixture.get("point",(0,0))
                p["client_width"],p["client_height"]=size
                p["drop_bytes"]=fixture.get("drop_bytes",b"")
                p["caps_guard"]=fixture.get("caps_guard",-1)
                settings["keyboard_disabled"]=bool(fixture.get("keyboard_disabled",0))
                system["input"]["sample"](lua.table_from({"x":point[0],"y":point[1],
                    "cursor_available":bool(fixture.get("enabled",1))and fixture.get("success",True)}))
                home=fixture.get("home",(0,0))
                system["providers"]["$grp_home"](vm,lua.table_from(home))
                try:
                    expected=oracle.call_control(hash_,args,discard,**fixture)
                    result=table_value(machine["call"](int(hash_,16),lua.table_from(args),discard))or[]
                    if result!=expected["result"]:reason=f"result {result} != {expected['result']}"
                    if hash_=="42d7c922":
                        raw=lua.eval("function(p)return(p.drop_bytes:gsub('.',function(c)return string.format('%02x',c:byte())end))end")(p)
                        if p["drag_accept"]!=expected["drag_flag"]or raw!=expected["drop_bytes"]:
                            reason="drag state or raw buffer differs"
                    if hash_ in("d5999d46","05b1a6e8"):
                        if bool(settings["keyboard_disabled"])!=bool(expected["keyboard_disabled"]):
                            reason="keyboard enable flag differs"
                    if hash_ in("125cfbdb","05b1a6e8"):
                        source=[row for row in expected["platform_calls"]if row["api"]=="keybd_event"]
                        emitted=[row for row in actual_calls if row["action"]=="caps_set"]
                        if len(emitted)!=(1 if source else 0):reason="caps toggle request differs"
                        if emitted and emitted[0]["args"]!=[int(args[0]!=0)if args else 0]:reason="caps target differs"
                    if hash_=="d4590bd3":
                        emitted=[row for row in actual_calls if row["action"]=="cursor_move"]
                        target=[expected["moved"][0]-41,expected["moved"][1]+23]
                        signed=lambda v:((v+2147483648)%4294967296)-2147483648
                        target=[signed(value)for value in target]
                        if len(emitted)!=1 or emitted[0]["args"]!=target:
                            reason=f"cursor request {emitted} != {target}"
                except Exception as error:reason=str(error)
                if reason:
                    failed[hash_]+=1
                    if len(report["failures"])<40:report["failures"].append({
                        "sample":sample["sample"],"hash":hash_,"args":args,
                        "fixture":{k:v.hex()if isinstance(v,bytes)else v for k,v in fixture.items()},
                        "discard":discard,"reason":reason})
        calls=[{"hash":h,"name":oracle.hash_entries[h]["name"],"cases":counts[h],"failures":failed[h],
                "argument_counts":sorted(arities[h]),"discard_modes":[False,True],
                "status":"passed"if not failed[h]else"failed"}for h in sorted(counts)]
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                                  "profile":profile,"calls":calls})
        print(sample["sample"],sum(counts.values()),"platform provider cases;",
              sum(failed.values()),"mismatches",flush=True)
    lua,_,_,system=registered_providers(overrides={"platform":lambda action,args:0})
    mod=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf8"))
    ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf8"))
    machine=lua.eval(FIXTURE)(mod,ops,system);vm=machine["vm"]
    sample_event=lambda values:system["input"]["sample"](lua.table_from(values,recursive=True))
    sample_event({"pressed":{13:True},"down":{17:True}})
    system["providers"]["$keybd_enable"](vm,lua.table())
    assert not list(system["state"]["input"]["pressed"].values())
    assert not list(system["state"]["input"]["down"].values())
    system["providers"]["$keybd_disable"](vm,lua.table())
    sample_event({"pressed":{13:True},"down":{17:True},"buttons":1})
    assert not list(system["state"]["input"]["pressed"].values())
    assert not list(system["state"]["input"]["down"].values())
    assert system["state"]["input"]["buttons"]==1
    system["providers"]["$keybd_enable"](vm,lua.table())
    sample_event({"pressed":{13:True},"down":{17:True}})
    assert system["state"]["input"]["pressed"][13]and system["state"]["input"]["down"][17]
    report["local_checks"].append("keyboard disable suppresses sampled key events while mouse buttons remain available")
    for name,args in(("$get_caps_stat",()),("$set_caps_stat",(1,)),("$mouse_move",(1,2))):
        fresh,_,_,missing=registered_providers()
        module=fresh.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf8"))
        opcodes=fresh.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf8"))
        target=fresh.eval(FIXTURE)(module,opcodes,missing)["vm"]
        accepted=fresh.eval("function(fn,vm,args)return pcall(fn,vm,args)end")(
            missing["providers"][name],target,fresh.table_from(args))
        assert not accepted[0]and"platform host provider missing"in str(accepted[1])
    report["local_checks"].append("caps and physical cursor providers fail explicitly when the platform host is absent")
    report["comparisons"]=sum(c["cases"]for s in report["samples"]for c in s["calls"])
    report["mismatches"]=sum(c["failures"]for s in report["samples"]for c in s["calls"])
    report["source_changed_during_test"]=before!=source_identity()
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_platform_controls.py","test_platform_control_recovery.py",
                    "test_platform_control_semantics.py","original_screen.py","original_calls.py",
                    "original_strings.py","original_opcode.py")}
    folder=archive_dir("platform_control_semantics")
    write_json(folder/(folder.name[:8]+"_platform_control_semantics.json"),report)
    write_json(ROOT/"reports/platform_control_semantics_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
