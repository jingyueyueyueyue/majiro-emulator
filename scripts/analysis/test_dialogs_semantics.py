"""Compare production async dialog providers with the fifteen original wrappers."""
from __future__ import annotations
from player_paths import player_path

import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers,source_identity
from original_dialogs import DialogOracle
from original_modes import ModeOracle

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,sha256,timestamp,write_json


def run():
    before=source_identity();folder=archive_dir("dialog_semantics")
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    dispatch={row["sample"]:row["entries"]for row in
              json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]}
    report={"schema":1,"timestamp":timestamp(),"source_identity":before,"samples":[],"failures":[],
            "scope":"production async providers versus all native dialog wrappers; typed formatting, "
                "pending/reply ownership, production suppression and retained/discarded results",
            "limits":["Android physical UI/lifecycle is separate; JVM ownership checks accompany this suite",
                      "original CRT formatting uses explicit host conversion boundary"],
            "complete_engine_semantics":False}
    fixtures=[[],["message"],["100% literal",17],["A",-2147483648,3.25],
              ["B",-1,0.0,"",2147483647],list(range(11)),
              ["A",1,1.5,"B",-2,-0.0,"C",3,1.234567,"D",4,5.25],
              [{"native_type":3},{"native_type":4},{"native_type":5},"\u30c6\u30b9\u30c8"]]
    for sample in samples:
        oracle=DialogOracle(sample,dispatch[sample["sample"]]);calls=[]
        mode_reference=ModeOracle(sample,dispatch[sample["sample"]])
        for address in mode_reference.mode_slots.values():oracle.write_u32(address,1)
        oracle.call_dialog("$dialog_ok",["calibrate"])
        mode_values={oracle.read_u32(address)for address in mode_reference.mode_slots.values()}
        assert mode_values in({0},{1}),"unclassified dialog playback-mode behavior"
        controls={"reset_playback_modes":mode_values=={0}}
        for address in mode_reference.mode_slots.values():oracle.write_u32(address,1)
        oracle.call_dialog("$dialog_error_ok",["calibrate"])
        error_values={oracle.read_u32(address)for address in mode_reference.mode_slots.values()}
        assert error_values in({0},{1}),"unclassified error-dialog playback-mode behavior"
        controls["reset_error_playback_modes"]=error_values=={0}
        controls["warning_retrycancel_flags"]=oracle.call_dialog(
            "$dialog_warning_retrycancel",["calibrate"])["dialogs"][0]["flags"]
        for mode in(-1,0,1,2):
            host_state={"request":None,"answer":None,"polls":0}
            def host(action,token,flags,message,caption):
                if action=="begin":
                    assert host_state["request"]is None
                    host_state["request"]={"token":token,"flags":flags,"message":message,"caption":caption}
                    host_state["polls"]=0;return"pending"
                assert action=="poll"and host_state["request"]["token"]==token
                host_state["polls"]+=1
                if host_state["polls"]==1:return"pending"
                host_state["request"]=None
                return"result "+str(host_state["answer"])
            lua,_,_,system=registered_providers(overrides={"dialog":host,"testplay":mode!=1,
                "dialog_controls":controls,
                "environment":{"TITLEBAR":oracle.caption.decode("ascii")}})
            module=lambda name:lua.execute(player_path(name).read_text(encoding="utf8"))
            vm=lua.eval("""function(mod,ops,system)
              local script={name='dialog-fixture.mjo',source_sha256=string.rep('b',64),
                main=0,functions={},code={{0,0x82c,0}}}
              local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=system.invoke,
                resume_action=system.resume_action,string_ops=system.strings}
              vm:begin(script.name);return vm
            end""")(module("runtime/majiro_vm.lua"),module("resources/scripts/opcodes.lua"),system)
            for name,entry in oracle.entries.items():
                if not name.startswith("$dialog_")or"input"in name:continue
                call=next((row for row in calls if row["name"]==name),None)
                if call is None:
                    call={"name":name,"hash":entry["hash"],"cases":0,"failures":0,
                          "argument_counts":sorted({len(args)for args in fixtures}),"discard_modes":[False,True]}
                    calls.append(call)
                for args in fixtures:
                    values=[];types=[]
                    for value in args:
                        if isinstance(value,dict):
                            tag=value["native_type"];value={"kind":"array","element_type":tag-3,
                                                          "dims":[1],"values":{}}
                        else:tag=2 if isinstance(value,str)else 1 if isinstance(value,float)else 0
                        values.append(value);types.append(tag)
                    parameters=lua.table_from(values,recursive=True);parameters["_types"]=lua.table_from(types)
                    for answer in(1,2,4,6,7)if mode in(0,1)else(6,):
                        for discard in(False,True):
                            call["cases"]+=1;oracle.answer=answer;host_state["answer"]=answer
                            reason=None
                            try:
                                for address in mode_reference.mode_slots.values():oracle.write_u32(address,1)
                                reference=oracle.call_dialog(name,args,discard=discard,mode=mode)
                                initial=len(vm["stack"]);saved=system["state"]["settings"]["save"]
                                for flag in("auto_mode","fast_mode","fast_read_mode","ultra_fast_mode"):
                                    system["state"]["settings"][flag]=True
                                result=system["providers"][name](vm,parameters,not discard)
                                if reference["dialogs"]:
                                    assert result==(None,True)and system["dialogs"]["active"]()
                                    request=host_state["request"];native=reference["dialogs"][0]
                                    assert request["message"]==native["message"]and request["flags"]==native["flags"]
                                    assert request["caption"]==native["caption"]
                                    assert system["state"]["settings"]["save"]is False
                                    reset=controls["reset_error_playback_modes"if name.startswith("$dialog_error_")
                                                   else"reset_playback_modes"]
                                    for flag in("auto_mode","fast_mode","fast_read_mode","ultra_fast_mode"):
                                        assert system["state"]["settings"][flag]==(not reset)
                                        if flag in mode_reference.mode_slots:
                                            assert oracle.read_u32(mode_reference.mode_slots[flag])==int(not reset)
                                    steps=vm["steps"]
                                    assert vm["run"](vm,100)=="waiting"and vm["steps"]==steps
                                    system["poll"](vm,lua.table())
                                    assert system["dialogs"]["active"]()and len(vm["stack"])==initial
                                    system["poll"](vm,lua.table())
                                    assert not system["dialogs"]["active"]()and vm["waiting"]is None
                                    assert vm["modal_dialog"]is None
                                    assert system["state"]["settings"]["save"]==saved
                                    actual=[]if discard else[(0,vm["pop"](vm))]
                                else:
                                    actual=[]if discard else[(0,result)]
                                    for flag in("auto_mode","fast_mode","fast_read_mode","ultra_fast_mode"):
                                        assert system["state"]["settings"][flag]is True
                                        if flag in mode_reference.mode_slots:
                                            assert oracle.read_u32(mode_reference.mode_slots[flag])==1
                                assert actual==reference["stack"],(actual,reference)
                                assert len(vm["stack"])==initial and host_state["request"]is None
                            except Exception as error:
                                reason=repr(error);call["failures"]+=1
                                if len(report["failures"])<30:report["failures"].append({
                                    "sample":sample["sample"],"name":name,"mode":mode,"argc":len(args),
                                    "answer":answer,"discard":discard,"reason":reason})
                                host_state["request"]=None
                call["status"]="passed"if not call["failures"]else"failed"
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],"calls":calls,
                                  "dialog_controls":controls})
        print(f"{sample['sample']}: {sum(call['cases']for call in calls)} async dialog cases; "
              f"{sum(call['failures']for call in calls)} mismatches",flush=True)
    report["comparisons"]=sum(call["cases"]for row in report["samples"]for call in row["calls"])
    report["mismatches"]=sum(call["failures"]for row in report["samples"]for call in row["calls"])
    report["test_sources"]={name:sha256(ROOT/"scripts/analysis"/name)for name in(
        "original_opcode.py","original_strings.py","original_calls.py","original_text.py",
        "original_dialogs.py","original_modes.py","native_function.py","test_dialogs_semantics.py")}
    report["source_changed_during_test"]=source_identity()!=before
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    write_json(folder/(folder.name[:8]+"_dialog_semantics.json"),report)
    write_json(ROOT/"reports/dialog_semantics_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
