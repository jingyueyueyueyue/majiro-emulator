"""Compare event registration order, ownership, duplicate release and guards."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
import zlib

from audit_player_semantics import registered_providers, source_identity
from original_events import EventOracle
from original_fixture_scope import finish_scope

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    by_name={row["sample"]:row for row in dispatch}
    before=source_identity()
    report={"schema":1,"timestamp":timestamp(),"samples":[],"failures":[],
        "source_identity":before,"complete_engine_semantics":False,
        "scope":"event list registration order, per-owner release, alias targets, protected nodes and startup lock",
        "limits":["startup protection lifetime and event dispatch/execution are separate validation targets"],
        "host_boundaries":["bounded guest heap","initialized CRT","Windows CP932 NLS"]}
    programs=[
        [("$event_release","FIXTURE",0x12345678,1)],
        [("$event_hook","FIXTURE",0x12345678,1),("$event_hook","FIXTURE",0x12345679,1),
         ("$event_hook_after","FIXTURE",0x1234567A,1)],
        [("$event_hook","FIXTURE",0x12345678,1),("$event_hook","FIXTURE",0x12345678,1),
         ("$event_hook","OTHER",0x12345678,1),("$event_release","FIXTURE",0x12345678,1),
         ("$event_release","FIXTURE",0x12345678,1)],
        [("$event_hook","FIXTURE",0x12345678,1),("$event_release","FIXTURE",0x12345679,1),
         ("$event_release","fixture",0x12345678,1),("$event_release","FIXTURE",0x1234567A,1)],
        [("$event_hook","FIXTURE",0x12345678,1),("$event_hook","FIXTURE",0x12345678,2),
         ("$event_release","FIXTURE",0x12345678,1),("$event_release","FIXTURE",0x12345678,2)],
        [("$event_hook","FIXTURE",0x12345678,1),("protect","FIXTURE",0x12345678,1),
         ("$event_release","FIXTURE",0x12345678,1)],
        [("$event_hook","FIXTURE",0x12345678,1),("lock","FIXTURE",0x12345678,1),
         ("$event_release","FIXTURE",0x12345678,1),("unlock","FIXTURE",0x12345678,1),
         ("$event_release","FIXTURE",0x12345678,1)],
    ]
    for sample in samples:
        try:
            oracle=EventOracle(sample,by_name[sample["sample"]]["entries"])
        except ValueError as error:
            # Different original list layouts must not abort coverage of
            # subsequent builds or be counted as a portable runtime failure.
            report.setdefault("unsupported_fixtures",[]).append({
                "sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                "status":"unsupported_fixture","reason":str(error),
                "runtime_behavior_verified":False})
            print(sample["sample"]+": original event layout unresolved; continuing other builds",flush=True)
            continue
        counts,failures=Counter(),Counter()
        for program,discard in[(program,discard)for program in programs for discard in(False,True)]:
            oracle.reset_events()
            lua,_,_,system=registered_providers()
            mod=lua.execute((ROOT/"player/runtime/majiro_vm.lua").read_text(encoding="utf-8"))
            ops=lua.execute((ROOT/"player/tables/opcodes.lua").read_text(encoding="utf-8"))
            vm=lua.eval("""
            function(mod,ops)
              local script={name='event-fixture',main=0,
                functions={{0x12345678,100},{0x12345679,120},{0x1234567a,100},{99,140}},
                code={{0,0x191},{100,0x191},{120,0x191},{140,0x191}}}
              local vm=mod.new{opcodes=ops,loader=function()return script end,
                syscall=function()error('unexpected event fixture syscall')end}
              vm:begin(script.name)
              assert(vm:spawn(99,{},false)==2)
              return vm
            end
            """)(mod,ops)
            locked=False
            for name,event,function,owner in program:
                if name in("lock","unlock"):
                    locked=name=="lock"
                    system["state"]["event_release_locked"]=locked
                    continue
                if name=="protect":
                    for row in oracle.event_rows():
                        oracle.write_u32(row["node"]+4,1)
                    for _,hooks in system["state"]["events"].items():
                        for _,hook in hooks.items():hook["protected"]=True
                    continue
                counts[name]+=1;reason=None
                try:
                    expected=oracle.call_event_api(name,event,function,owner,locked,discard)
                    vm["select_thread"](vm,owner)
                    actual=system["providers"][name]
                    if actual is None:raise RuntimeError("runtime provider missing: "+name)
                    value=actual(vm,lua.table_from([event,function]))
                    if expected!=([]if discard else[(0,0)])or value!=0:reason="event API result differs"
                    native=defaultdict(list)
                    # Event frames are stacked in original list order and
                    # consequently execute in the reverse order.
                    for row in reversed(oracle.event_rows()):
                        native[row["event_hash"]].append((row["owner"],row["offset"],bool(row["protected"])))
                    portable=defaultdict(list)
                    for event_name,hooks in system["state"]["events"].items():
                        key=zlib.crc32(event_name.encode("cp932"))
                        for _,hook in sorted(hooks.items()):
                            portable[key].append((hook["owner"]or 1,hook["offset"],bool(hook["protected"])))
                    if dict(native)!=dict(portable):
                        reason=f"event lists differ: native={dict(native)}, portable={dict(portable)}"
                except Exception as error:reason=str(error)
                if reason:
                    failures[name]+=1
                    if failures[name]<=12:report["failures"].append({"sample":sample["sample"],
                        "name":name,"event":event,"function":f"{function:08x}","owner":owner,
                        "discard":discard,"reason":reason})
        results=[{"hash":oracle.entries[name]["hash"],"name":name,"cases":counts[name],
            "failures":failures[name],"status":"passed"if not failures[name]else"failed",
            "argument_counts":[2],"discard_modes":[False,True]}for name in sorted(counts)]
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "calls":results,"native_list_head":oracle.event_head,"native_release_core":oracle.release_va})
        print(f"{sample['sample']}: {sum(counts.values())} event operations; {sum(failures.values())} mismatches",flush=True)
    report["comparisons"]=sum(call["cases"]for row in report["samples"]for call in row["calls"])
    report["mismatches"]=sum(call["failures"]for row in report["samples"]for call in row["calls"])
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","original_calls.py",
                    "original_events.py","test_event_syscalls.py")}
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    finish_scope(report)
    folder=archive_dir("event_syscalls")
    write_json(folder/(folder.name[:8]+"_event_syscalls.json"),report)
    write_json(ROOT/"reports/event_syscall_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
