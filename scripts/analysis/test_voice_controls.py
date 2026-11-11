"""Compare real v-tag decoding and API formatting with original voice entry."""
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
from original_voice_control import VoiceControlOracle

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


FIXTURE="""
function(mod,ops,system)
  local requests={}
  system.state.media.voice_tag=function(vm,filename,force,mode,male,skip)
    requests[#requests+1]={filename=filename,force=force,mode=mode,male=male,skip=skip}
    return 0
  end
  return function(hash,tag,args,male,skip,discard)
    for index=#requests,1,-1 do requests[index]=nil end
    system.state.pending_voice_male=male~=0 and male or false
    system.state.pending_voice_skip=skip~=0 and skip or false
    system.state.media.voice_requested='prior'
    local row=hash and{0,discard and 0x835 or 0x834,hash,#args}or{0,0x842,tag}
    local script={name='voice-control-fixture',main=0,functions={},code={row,{4096,0x191}}}
    local vm=mod.new{opcodes=ops,loader=function()return script end,syscall=system.invoke,
      text_ops=system.text,string_ops=system.strings}
    vm:begin(script.name)
    for index=#args,1,-1 do vm:push(args[index])end
    vm:step()
    local function flag(value)return value==true and 1 or value or 0 end
    return {state={requests=requests,male=flag(system.state.pending_voice_male),
      skip=flag(system.state.pending_voice_skip),
      requested=system.providers['$voice_get_fn$'](vm,{})},stack=vm.stack}
  end
end
"""


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    by_name={row["sample"]:row for row in dispatch};before=source_identity()
    report={"schema":1,"timestamp":timestamp(),"source_identity":before,"samples":[],"failures":[],
        "vm_sha256":before["files"]["player/runtime/majiro_vm.lua"],
        "scope":"v-tag force byte and filename, API decimal formatting, script/API mode, one-shot male/skip flags, skip default, API filename metadata and disabled-device guard",
        "host_boundaries":["original downstream voice entry","explicit API device capability",
                           "host MSVCRT decimal formatting","cached configuration values"],
        "limits":["downstream playback/mute/gender/ducking behavior and raw voice transport need separate native comparisons"],
        "complete_engine_semantics":False}
    raw_tags=["v0voice","v1voice","v9\u3042","v-1voice","vAvoice","v0##nw##","v0"]
    programs=[("ctrl",tag,[],False)for tag in raw_tags]
    programs += [("api",None,[filename]if force is None else[filename,force],discard)
                 for filename,force,discard in itertools.product(
                     ("","voice","sub\\voice","\u3042","##nw##"),
                     (None,0,1,9,-1,10,1000),(False,True))]
    for sample in samples:
        oracle=VoiceControlOracle(sample,by_name[sample["sample"]]["entries"])
        counts,failures=Counter(),Counter();arities=set()
        for default,disabled in itertools.product((0,1,2),(False,True)):
            lua,_,_,system=registered_providers(overrides={
                "environment":{"VOICESKIPDEF":str(default)},"audio_disabled":disabled})
            module=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
            ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
            execute=lua.eval(FIXTURE)(module,ops,system)
            for male,skip,program in itertools.product((0,1,2),(0,1,-1),programs):
                kind,tag,args,discard=program;counts[kind]+=1;reason=None
                if kind=="api":arities.add(len(args))
                try:
                    oracle.reset_control(male,skip,default,disabled)
                    if kind=="api":
                        expected=oracle.call("$voice",args,discard)
                        state=oracle.control_state();hash_=int(oracle.entries["$voice"]["hash"],16)
                    else:
                        state=oracle.control(tag);expected=[];hash_=None
                    actual=table_value(execute(hash_,tag,lua.table_from(args),male,skip,discard))
                    actual["state"]["requests"]=actual["state"]["requests"]or[]
                    if actual["state"]!=state:reason=f"state {actual['state']} != {state}"
                    elif(actual["stack"]or[])!=[native_number(tag_,raw)for tag_,raw in expected]:
                        reason="voice control operand stack differs"
                except Exception as error:reason=repr(error)
                if reason:
                    failures[kind]+=1
                    if len(report["failures"])<40:report["failures"].append({
                        "sample":sample["sample"],"kind":kind,"tag":tag,"args":args,
                        "male":male,"skip":skip,"default":default,"disabled":disabled,
                        "discard":discard,"reason":reason})
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "cases":{"ctrl":counts["ctrl"]},"failures":{"ctrl":failures["ctrl"]},
            "passed_opcodes":["ctrl"]if not failures["ctrl"]else[],
            "calls":[{"name":"$voice","hash":oracle.entries["$voice"]["hash"],
                "cases":counts["api"],"failures":failures["api"],
                "status":"passed"if not failures["api"]else"failed",
                "argument_counts":sorted(arities),"discard_modes":[False,True]}],
            "voice_entry":oracle.voice_va,"voice_fastcall":oracle.voice_fastcall,
            "skip_default_slot":oracle.skip_default_slot,"api_guard":oracle.guard_va})
        print(f"{sample['sample']}: {sum(counts.values())} voice controls; "
              f"{sum(failures.values())} mismatches",flush=True)
    report["comparisons"]=sum(sum(row["cases"].values())+sum(c["cases"]for c in row["calls"])
        for row in report["samples"])
    report["mismatches"]=sum(sum(row["failures"].values())+sum(c["failures"]for c in row["calls"])
        for row in report["samples"])
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","original_calls.py","native_function.py",
                    "original_text.py","original_voice_settings.py","original_voice_control.py",
                    "test_voice_controls.py")}
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    folder=archive_dir("voice_controls")
    write_json(folder/(folder.name[:8]+"_voice_controls.json"),report)
    write_json(ROOT/"reports/voice_control_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
