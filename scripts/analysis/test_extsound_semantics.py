"""Original four-channel intro/loop routing, failure and suppression semantics."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers,source_identity
from original_extsound import ExtSoundOracle

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT,archive_dir,timestamp,write_json


def run():
    samples=json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch=json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    by_name={row["sample"]:row for row in dispatch};before=source_identity()
    files={"intro.ogg","loop.ogg","both.wav","both.ogg","\u97f3.ogg"}
    requests=[("intro","loop"),("intro",""),("both","loop"),("missing","loop"),("intro","missing"),
              ("","loop"),("sub\\Intro","LOOP"),("\u97f3","loop")]
    cases=[(first,second,False,False,1000,1000)for first,second in requests]
    cases += [("intro","loop",disabled,ultra,master,gain)
              for disabled in(False,True)for ultra in(False,True)
              for master in(0,700,1000)for gain in(400,1000)]
    report={"schema":1,"timestamp":timestamp(),"source_identity":before,"samples":[],"failures":[],
        "scope":"extended SE channels, actual WAV/OGG resolution order, previous-stream stop, mute/ultra suppression and gain reset",
        "host_boundaries":["single-thread critical sections","explicit device capability",
                           "fixture VFS membership","host MSVCRT CP932 path splitting","final audio decoder entry"],
        "limits":["decoder output, pan/fade interaction and physical handoff are separate validation targets"],
        "complete_engine_semantics":False}
    for sample in samples:
        oracle=ExtSoundOracle(sample,by_name[sample["sample"]]["entries"])
        counts,failures=Counter(),Counter()
        for channel,info in oracle.channels.items():
            for first,second,disabled,ultra,master,gain in cases:
                for previous in(False,True):
                    for discard in(False,True):
                        counts[info["api"]]+=1;events=[];asset_reads=[];lua_ref={}
                        def asset(name):
                            asset_reads.append(name)
                            return lua_ref["lua"].table_from({"file":name,"duration_ms":100})
                        def audio(action,spec):
                            events.append({"action":action,"id":spec["id"],"file":spec["file"],
                                "loop_file":spec["loop_file"]or"","gain":spec["volume"],
                                "loop":bool(spec["loop"])})
                            return True
                        lua,_,_,system=registered_providers(overrides={
                            "resource_names":{name:True for name in files},"media_asset":asset,
                            "environment":{"SOUNDVOL":str(master)},"audio":audio,"audio_disabled":disabled})
                        lua_ref["lua"]=lua;media=system["state"]["media"]
                        media[channel+"_volume"]=gain
                        if previous:
                            media["channels"][channel]=lua.table_from({"id":channel,"file":"previous.ogg",
                                "loop":True,"volume":1000,"generation":0,"started":0,"duration_ms":100})
                        system["state"]["settings"]["ultra_fast_mode"]=ultra
                        vm=lua.table();vm["frame"]=lambda current:lua.table_from({"script":{"name":"fixture"}},recursive=True)
                        reason=None
                        try:
                            expected=oracle.call_extsound(channel,first,second,files,previous,
                                                         master,gain,ultra,disabled,discard)
                            provider=system["providers"][info["api"]]
                            if provider is None:raise RuntimeError("extended sound provider missing")
                            actual=provider(vm,lua.table_from([first,second]))
                            if actual!=0 or expected["result"]!=([]if discard else[(0,0)]):
                                reason="extended SE result or native stack cleanup differs"
                            plays=[entry for entry in events if entry["action"]=="play"]
                            stops=[entry for entry in events if entry["action"]=="stop"]
                            if not reason and len(plays)!=len(expected["plays"]):reason="playback suppression differs"
                            if not reason and bool(stops)!=(previous and not expected["old_active"]):
                                reason="previous channel stop differs"
                            if not reason and plays:
                                a,b=plays[0],expected["plays"][0]
                                if(a["id"]!=channel or a["file"].lower()!=b["first"].lower()
                                   or a["loop_file"].lower()!=b["second"].lower()
                                   or a["gain"]!=b["gain"]or int(a["loop"])!=b["loop"]):
                                    reason=f"decoder request {a} != {b}"
                            expected_reads=len(expected["plays"])*(2 if second else 1)
                            if not reason and len(asset_reads)!=expected_reads:
                                reason="unplayed or incomplete sound unnecessarily requested decoding"
                            if not reason and media[channel+"_volume"]!=expected["next_gain"]:
                                reason=f"next-channel gain {media[channel+'_volume']} != {expected['next_gain']}"
                        except Exception as error:reason=str(error)
                        if reason:
                            failures[info["api"]]+=1
                            if failures[info["api"]]<=12:report["failures"].append({
                                "sample":sample["sample"],"name":info["api"],"first":first,"second":second,
                                "disabled":disabled,"ultra":ultra,"master":master,"gain":gain,
                                "previous":previous,"discard":discard,"reason":reason})
        calls=[{"hash":oracle.entries[name]["hash"],"name":name,"cases":counts[name],
                "failures":failures[name],"status":"passed"if not failures[name]else"failed",
                "argument_counts":[2],"discard_modes":[False,True]}for name in sorted(counts)]
        report["samples"].append({"sample":sample["sample"],"exe_sha256":sample["exe_sha256"],
            "calls":calls,"vfs_predicate":oracle.exists_va,"splitpath_boundary":oracle.splitpath_va,
            "decoder_boundary":oracle.play_va,"decoder_stack_arguments":oracle.play_cleanup//4,
            "master_slot":oracle.master_slot,"enabled_slot":oracle.enabled_slot})
        print(f"{sample['sample']}: {sum(counts.values())} extended sounds; "
              f"{sum(failures.values())} mismatches",flush=True)
    report["comparisons"]=sum(row["cases"]for sample in report["samples"]for row in sample["calls"])
    report["mismatches"]=sum(row["failures"]for sample in report["samples"]for row in sample["calls"])
    report["source_changed_during_test"]=source_identity()!=before
    report["test_sources"]={name:hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in("original_opcode.py","original_strings.py","original_calls.py","native_function.py",
                    "original_audio.py","original_extsound.py","test_extsound_semantics.py")}
    report["status"]="passed"if not report["mismatches"]and not report["source_changed_during_test"]else"failed"
    folder=archive_dir("extsound_semantics")
    write_json(folder/(folder.name[:8]+"_extsound_semantics.json"),report)
    write_json(ROOT/"reports/extsound_semantics_tests.json",report)
    return 0 if report["status"]=="passed"else 1


if __name__=="__main__":raise SystemExit(run())
