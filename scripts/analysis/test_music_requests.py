"""Original music pause/resume request sequencing before worker service."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers,source_identity
from original_audio import AudioOracle

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    by_name={row["sample"]:row for row in dispatch};before=source_identity()
    programs=[["$music_pause"],["$music_resume"],["$music_pause","$music_pause"],
              ["$music_pause","$music_resume","$music_pause"],
              ["$music_resume","$music_pause","$music_resume"],
              ["$music_pause","$music_resume","$music_resume"]]
    report={"schema":1,"timestamp":timestamp(),"source_identity":before,"samples":[],"failures":[],
        "scope":"music handle lookup, pause/resume request flags, repeated commands, absent channels and return disposal",
        "host_boundaries":["single-thread critical sections"],
        "limits":["audio worker scheduling and actual device pause/resume are verified separately"],
        "complete_engine_semantics":False}
    for sample in samples:
        oracle=AudioOracle(sample,by_name[sample["sample"]]["entries"])
        counts,failures=Counter(),Counter()
        for program in programs:
            for present in(False,True):
                for initial in((False,False),(True,False),(True,True)):
                    for discard in(False,True):
                        oracle.reset_music(present,*initial)
                        lua,_,_,system=registered_providers()
                        vm=lua.table()
                        spec=lua.table_from({"id":"music","pause_requested":initial[0],
                                             "resume_requested":initial[1]})
                        if present:system["state"]["media"]["channels"]["music"]=spec
                        for name in program:
                            counts[name]+=1;reason=None
                            try:
                                expected=oracle.call_music(name,discard)
                                provider=system["providers"][name]
                                if provider is None:raise RuntimeError("music request provider missing")
                                actual=provider(vm,lua.table())
                                if expected!=([]if discard else[(0,0)])or actual!=0:
                                    reason="music request result differs"
                                elif present and oracle.music_requests()!=(bool(spec["pause_requested"]),bool(spec["resume_requested"])):
                                    reason=f"request flags {oracle.music_requests()} differ from portable flags"
                                elif not present and system["state"]["media"]["channels"]["music"]is not None:
                                    reason="absent music acquired a synthetic channel"
                            except Exception as error:reason=str(error)
                            if reason:
                                failures[name]+=1
                                if failures[name]<=12:report["failures"].append({"sample":sample["sample"],
                                    "name":name,"program":program,"present":present,
                                    "initial":initial,"discard":discard,"reason":reason})
        calls=[{"hash":oracle.entries[name]["hash"],"name":name,"cases":counts[name],
                "failures":failures[name],"status":"passed"if not failures[name]else"failed",
                "argument_counts":[0],"discard_modes":[False,True]}for name in sorted(counts)]
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "calls":calls,"stream_head":oracle.stream_head,"music_handle_slot":oracle.music_slot,
            "stream_next_offset":oracle.next_offset,"critical_section_calls":oracle.critical_calls})
        print(f"{sample['sample']}: {sum(counts.values())} music requests; {sum(failures.values())} mismatches",flush=True)
    report["comparisons"]=sum(row["cases"]for sample in report["samples"]for row in sample["calls"])
    report["mismatches"]=sum(row["failures"]for sample in report["samples"]for row in sample["calls"])
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","original_calls.py","native_function.py",
                    "original_audio.py","test_music_requests.py")}
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    folder=archive_dir("music_requests")
    write_json(folder/(folder.name[:8]+"_music_requests.json"),report)
    write_json(ROOT/"reports/music_request_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
