"""Verify ultra eligibility independently of ordinary skip permission and state."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers,source_identity
from original_modes import ModeOracle
from original_opcode import native_number

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    by_name={row["sample"]:row for row in dispatch};before=source_identity()
    profiles=json.loads((ROOT/"config/player_engine_profiles.json").read_text(encoding="utf-8"))
    report={"schema":1,"timestamp":timestamp(),"samples":[],"failures":[],
        "source_identity":before,"complete_engine_semantics":False,
        "scope":"ultra eligibility, independent playback flags and build-specific disable return",
        "host_boundaries":["native menu refresh","native keyboard queue reset"],
        "limits":["menu contents and actual input queues are separately verified"]}
    for sample in samples:
        oracle=ModeOracle(sample,by_name[sample["sample"]]["entries"])
        if "$ultra_fast_enable" not in oracle.entries:
            report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
                                      "calls":[],"absent_public_api":"ultra-fast family"})
            continue
        behavior=oracle.disable_returns_previous()
        calls=[]
        for name in("$ultra_fast_enable","$ultra_fast_disable","$set_ultra_fast_mode"):
            count=failed=0
            for allowed,discard in[(allowed,discard)for allowed in(0,1,7,-1)for discard in(False,True)]:
                for active in(False,True):
                    for skip_allowed in(False,True):
                        count+=1
                        modes={field:active for field in oracle.mode_slots}
                        lua,_,_,system=registered_providers(overrides={
                            "ultra_disable_returns_previous":behavior})
                        state=system["state"]["settings"]
                        for field,value in modes.items():state[field]=value
                        state["ultra_fast_allowed"]=allowed;state["fast_allowed"]=skip_allowed
                        reason=None
                        try:
                            expected=oracle.call_mode(name,allowed,modes,discard)
                            provider=system["providers"][name]
                            if provider is None:raise RuntimeError("missing ultra provider")
                            actual=provider(lua.table(),lua.table())
                            if expected!=([]if discard else[(0,int(actual)&0xffffffff)]):
                                reason=f"ultra API result {actual} != {expected}"
                            elif state["ultra_fast_allowed"]!=native_number(0,oracle.read_u32(oracle.allowed_slot)):
                                reason="ultra eligibility differs"
                            else:
                                for field,address in oracle.mode_slots.items():
                                    if bool(state[field])!=bool(oracle.read_u32(address)):
                                        reason="playback mode differs: "+field;break
                        except Exception as error:reason=str(error)
                        if reason:
                            failed+=1
                            if failed<=12:report["failures"].append({"sample":sample["sample"],
                                "name":name,"allowed":allowed,"modes_active":active,
                                "ordinary_skip_allowed":skip_allowed,"discard":discard,"reason":reason})
            calls.append({"hash":oracle.entries[name]["hash"],"name":name,"cases":count,"failures":failed,
                "status":"passed"if not failed else"failed","argument_counts":[0],"discard_modes":[False,True]})
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "calls":calls,"allowed_slot":oracle.allowed_slot,"mode_slots":oracle.mode_slots,
            "disable_returns_previous":behavior})
        print(f"{sample['sample']}: {sum(row['cases']for row in calls)} ultra checks; "
              f"{sum(row['failures']for row in calls)} mismatches",flush=True)
    report["comparisons"]=sum(row["cases"]for sample in report["samples"]for row in sample["calls"])
    report["mismatches"]=sum(row["failures"]for sample in report["samples"]for row in sample["calls"])
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","original_calls.py",
                    "original_modes.py","test_ultra_eligibility.py")}
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    folder=archive_dir("ultra_eligibility")
    write_json(folder/(folder.name[:8]+"_ultra_eligibility.json"),report)
    write_json(ROOT/"reports/ultra_eligibility_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
