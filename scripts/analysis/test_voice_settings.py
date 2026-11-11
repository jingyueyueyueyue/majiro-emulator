"""Compare label registration, per-character voice levels and one-shot flags."""
from __future__ import annotations

from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers,source_identity
from lua_bridge import table_value
from original_opcode import native_number
from original_voice_settings import VoiceSettingsOracle

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


EXECUTE="""
function(mod,ops,system,hash,args,discard)
  local script={name='voice-settings-fixture',main=0,functions={},
    code={{0,discard and 0x835 or 0x834,hash,#args},{8,0x191}}}
  local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=system.invoke}
  vm:begin(script.name)
  for index=#args,1,-1 do vm:push(args[index])end
  vm:step()
  return vm.stack
end
"""


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    by_name={row["sample"]:row for row in dispatch}
    before=source_identity()
    report={"schema":1,"timestamp":timestamp(),"source_identity":before,"samples":[],"failures":[],
        "scope":"30 voice labels, 15 text labels, replacement/clear, clamped per-character level lookup, female gate, ducking queries and one-shot request setters",
        "host_boundaries":["initialized CRT and bounded guest heap"],
        "limits":["voice playback, configuration dialogs and registry initialization are separate validation targets",
                  "overlong labels and table overflow rejected instead of reproducing memory corruption"],
        "complete_engine_semantics":False}
    for sample in samples:
        available=by_name[sample["sample"]]["entries"]
        if not any(row["name"]=="$get_voice_setup" for row in available):
            report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                "calls":[],"absent_public_api":"$get_voice_setup",
                "scope":"original dispatch lacks per-character voice settings family"})
            continue
        oracle=VoiceSettingsOracle(sample,by_name[sample["sample"]]["entries"])
        lua,_,_,system=registered_providers()
        module=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
        ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
        execute=lua.eval(EXECUTE)
        counts,failures,arities=Counter(),Counter(),{}
        levels=[(index*83-200)for index in range(30)]

        def check(name,args=(),discard=False,prepare=None,inspect=None):
            counts[name]+=1;arities.setdefault(name,set()).add(len(args))
            reason=None
            try:
                if prepare:prepare()
                expected=oracle.call(name,args,discard)
                actual=table_value(execute(module,ops,system,int(oracle.entries[name]["hash"],16),
                                          lua.table_from(args),discard))or[]
                values=[native_number(tag,raw)for tag,raw in expected]
                if actual!=values:reason=f"stack {actual} != {values}"
                elif inspect:reason=inspect()
            except Exception as error:reason=repr(error)
            if reason:
                failures[name]+=1
                if len(report["failures"])<50:report["failures"].append({
                    "sample":sample["sample"],"name":name,"argc":len(args),
                    "discard":discard,"reason":reason})

        for name,info in oracle.label_tables.items():
            field="voice_setup_labels"if name=="$voice_setup"else"voice_text_labels"
            programs=[[],["A"],["A","\u3042","\u30bd"],["x"*127],
                      [f"label_{i}"for i in range(info["capacity"])],["replacement"],[]]
            for discard in(False,True):
                for args in programs:
                    def inspect_labels(name=name,field=field):
                        actual=table_value(system["state"][field])
                        return None if actual==oracle.labels(name)else"label order or stale tail differs"
                    check(name,args,discard,inspect=inspect_labels)
        for female,index,discard in itertools.product((0,1,2,-1),
                (-2147483648,-1,0,1,15,28,29,30,2147483647),(False,True)):
            query_lua,_,_,query_system=registered_providers(overrides={"environment":{"ISTALKFEMALE":str(female)}})
            query_module=query_lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
            query_ops=query_lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
            query_execute=query_lua.eval(EXECUTE)
            oracle.set_levels(levels,female)
            query_system["state"]["settings"]["voice_setup_levels"]=query_lua.table_from(levels)
            name="$get_voice_setup";counts[name]+=1;arities.setdefault(name,set()).add(1)
            expected=oracle.call(name,[index],discard)
            actual=table_value(query_execute(query_module,query_ops,query_system,
                int(oracle.entries[name]["hash"],16),query_lua.table_from([index]),discard))or[]
            if actual!=[native_number(tag,raw)for tag,raw in expected]:
                failures[name]+=1;report["failures"].append({"sample":sample["sample"],"name":name,
                    "index":index,"female":female,"reason":"clamped voice level query differs"})
        for name,field in(("$voice_musicdown_vol","voice_musicdown_vol"),
                          ("$is_voice_musicdown","voice_musicdown")):
            if name not in oracle.entries:continue
            for value,discard in itertools.product((-1,0,1,500,1000,2147483647),(False,True)):
                def prepare_scalar():
                    oracle.write_u32(oracle.scalars[name],value)
                    system["state"]["settings"][field]=value
                check(name,(),discard,prepare_scalar)
        for name,field in(("$set_voice_male","pending_voice_male"),("$set_voice_skip","pending_voice_skip")):
            if name not in oracle.entries:continue
            for initial,args,discard in itertools.product((0,1),[(),(0,),(19,)],(False,True)):
                def prepare_flag():
                    oracle.write_u32(oracle.scalars[name],initial)
                    system["state"][field]=bool(initial)
                def inspect_flag():
                    return None if bool(oracle.scalar(name))==bool(system["state"][field])else"one-shot setter differs"
                check(name,args,discard,prepare_flag,inspect_flag)
        calls=[{"name":name,"hash":oracle.entries[name]["hash"],"cases":count,
                "failures":failures[name],"status":"passed"if not failures[name]else"failed",
                "argument_counts":sorted(arities[name]),"discard_modes":[False,True]}
               for name,count in sorted(counts.items())]
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "calls":calls,"label_tables":oracle.label_tables,"levels_table":oracle.levels,
            "female_slot":oracle.female_slot})
        print(f"{sample['sample']}: {sum(counts.values())} voice settings; {sum(failures.values())} mismatches",flush=True)
    report["comparisons"]=sum(call["cases"]for row in report["samples"]for call in row["calls"])
    report["mismatches"]=sum(call["failures"]for row in report["samples"]for call in row["calls"])
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","original_calls.py","native_function.py",
                    "original_text.py","original_voice_settings.py","test_voice_settings.py")}
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    folder=archive_dir("voice_settings")
    write_json(folder/(folder.name[:8]+"_voice_settings.json"),report)
    write_json(ROOT/"reports/voice_settings_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
