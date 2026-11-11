"""Batch missing audio-family semantics against all original sample EXEs."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers, source_identity
from original_audio_completion import AudioCompletionOracle
from lua_bridge import table_value

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT, archive_dir, timestamp, write_json

NAMES = ("$music_replace", "$extmusic_replace", "$music_fadeout_wait", "$music_stat_reset", "$sound_ch_wait")

def lifecycle_checks():
    clock={"now":0};fast={"value":False};reference={};events=[];tracks={}
    def audio(action,spec):
        key=spec["id"]
        events.append(action)
        if action in ("play","replace"):
            old=tracks.get(key)
            token=(old["token"]if old else 0)+1
            tracks[key]={"token":token,"playing":True,"completed":False}
        elif action=="playing":return bool(tracks.get(key,{}).get("playing"))
        elif action=="completed":return bool(tracks.get(key,{}).get("completed"))
        elif action=="stat_reset":
            if key in tracks:tracks[key]["completed"]=False
        elif action in ("stop","fadeout"):tracks.pop(key,None)
        return True
    def asset(name):return reference["lua"].table_from({"file":name,"duration_ms":100})
    lua,_,_,system=registered_providers(overrides={
        "now":lambda:clock["now"],"wall_now":lambda:clock["now"],
        "audio":audio,"media_asset":asset,"resource_names":{"fixture.wav":True,"loop.ogg":True}})
    reference["lua"]=lua
    system["providers"]["$is_fast_mode"]=lambda vm,args:int(fast["value"])
    system["state"]["settings"]["soundvolume"]=1000
    system["state"]["settings"]["voicevolume"]=1000
    vm=lua.table()
    vm["frame"]=lambda current:lua.table_from({"script":{"name":"audio-completion-lifecycle"}},recursive=True)
    media=system["state"]["media"]
    def call(name,*args):return system["providers"][name](vm,lua.table_from(args))
    call("$music","fixture",0)
    generation=media["channels"]["music"]["generation"]
    token=tracks["music"]["token"]
    call("$music_replace","loop",0)
    assert media["channels"]["music"]["generation"]==generation and tracks["music"]["token"]!=token
    assert media["channels"]["music"]["file"]=="loop.ogg"
    tracks["music"]["completed"]=True
    events.clear();call("$music_stat_reset")
    assert events==["stat_reset"]and not tracks["music"]["completed"]
    call("$music_fadeout_wait",150)
    assert vm["waiting"]["until_ms"]==150 and media["channels"]["music"]is None
    call("$music","fixture",0)
    assert vm["waiting"]["until_ms"]==150,"new music must not cancel old fade-time continuation"
    vm["waiting"]=None
    call("$sound_ch",7,"fixture",0);call("$sound_ch_wait",7)
    assert vm["waiting"]
    tracks["sound_ch_7"]["completed"]=True
    media["update_pending"](vm)
    assert vm["waiting"]is None
    tracks["sound_ch_7"]["completed"]=False
    call("$sound_ch_wait",7);fast["value"]=True
    media["update_pending"](vm)
    assert vm["waiting"]is None and media["channels"]["sound_ch_7"]is None
    return ["replacement retains logical generation and changes transport owner",
            "completion reset only clears the independent latch",
            "new music does not cancel a previous fade-time wait",
            "numbered wait releases on completion latch",
            "fast-mode polling stops and releases a waiting numbered channel"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="append")
    args = parser.parse_args()
    samples = json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf8"))["samples"]
    dispatch = {row["sample"]: row for row in json.loads(
        (ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf8"))["samples"]}
    if args.sample:
        samples = [row for row in samples if row["sample"] in args.sample]
    before = source_identity()
    report = {"schema": 1, "timestamp": timestamp(), "source_identity": before,
              "samples": [], "failures": [], "complete_engine_semantics": False,
              "scope": "music replacement stream requests, completion reset, fade deadlines and numbered waits",
              "host_boundaries": ["existing fixture VFS/decoder boundaries", "single-thread locks",
                                  "fixed script clock", "fast-mode query",
                                  "sound wait helper return boundary for pending native opcode"],
              "limits": ["physical decoder replacement and stale callback ownership require Android host tests",
                         "old EXEs normalize nonzero replacement loop flags to -1; runtime stores logical nonzero",
                         "pending native calls retain argument slots while portable VM owns a continuation"]}
    for sample in samples:
        oracle = AudioCompletionOracle(sample, dispatch[sample["sample"]]["entries"])
        cases = []
        for present in (False, True):
            for active, completed in ((False, False), (True, False), (True, True)):
                for fast in (False, True):
                    fixture = {"present": present, "active": active, "completed": completed, "fast": fast}
                    cases.append(("$music_stat_reset", [], fixture))
                    cases.append(("$sound_ch_wait", [7], fixture))
                    for duration in (-31, 0, 1, 150):
                        cases.append(("$music_fadeout_wait", [duration], fixture))
                    for arguments in (["fixture"], ["fixture", 0], ["fixture", 1],
                                      ["fixture", -2], ["missing"], [""]):
                        cases.append(("$music_replace", arguments, fixture))
                    for arguments in (["fixture", "loop"], ["fixture", ""], ["fixture", "missing"]):
                        cases.append(("$extmusic_replace", arguments, fixture))
        counts, failures, argument_counts = Counter(), Counter(), {}
        for name, arguments, fixture in cases:
            for discard in (False, True):
                counts[name] += 1
                argument_counts.setdefault(name, set()).add(len(arguments))
                events, reference = [], {}
                def audio(action, spec):
                    events.append({"action": action, "file": spec["file"], "loop_file": spec["loop_file"],
                                   "generation": spec["generation"], "loop": bool(spec["loop"]),
                                   "fade_ms": spec["fade_ms"]})
                    if action == "playing":
                        return fixture["present"] and fixture["active"]
                    if action == "completed":
                        return fixture["completed"]
                    return True
                def asset(filename):
                    return reference["lua"].table_from({"file": filename, "duration_ms": 100})
                lua, _, _, system = registered_providers(overrides={
                    "now": lambda: 2000, "wall_now": lambda: 2000, "audio": audio, "media_asset": asset,
                    "resource_names": {"fixture.wav": True, "loop.ogg": True}})
                reference["lua"] = lua
                system["providers"]["$is_fast_mode"] = lambda vm, args: int(fixture["fast"])
                vm = lua.table()
                vm["frame"] = lambda current: lua.table_from({"script": {"name": "completion-fixture"}}, recursive=True)
                media = system["state"]["media"]
                for channel in ("music", "sound_ch_7"):
                    if fixture["present"]:
                        media["channels"][channel] = lua.table_from({
                            "id": channel, "file": "previous.wav", "name": "previous",
                            "generation": 12, "duration_ms": 100, "started": 1900,
                            "channel_volume": 777, "volume": 777, "pan": 0, "loop": False,
                            "completed_latch": fixture["completed"]})
                reason = None
                try:
                    oracle.prepare_completion(**fixture)
                    expected = oracle.call_completion(name, arguments, discard)
                    actual = system["providers"][name](vm, lua.table_from(arguments))
                    if actual != 0:
                        reason = "portable completion API result differs"
                    elif name == "$sound_ch_wait":
                        if bool(vm["waiting"]) != expected["pending"]:
                            reason = f"wait pending {bool(vm['waiting'])} != {expected['pending']}"
                        elif fixture["fast"] and fixture["present"] and media["channels"]["sound_ch_7"] is not None:
                            reason = "fast wait failed to clear numbered channel"
                    elif name == "$music_fadeout_wait":
                        if bool(vm["waiting"]) != expected["pending"]:
                            reason = f"fade pending {bool(vm['waiting'])} != {expected['pending']}"
                        elif expected["pending"] and vm["waiting"]["until_ms"] != 2000 + (0 if fixture["fast"] else arguments[0]):
                            reason = "fade deadline differs"
                        elif expected["music_handle"] == 0 and media["channels"]["music"] is not None:
                            reason = "fade did not clear current handle"
                    elif name == "$music_stat_reset":
                        spec = media["channels"]["music"]
                        if spec and spec["completed_latch"] != expected["completed"]:
                            reason = "completion reset differs"
                        elif any(row["action"] in ("play", "stop", "replace") for row in events):
                            reason = "completion reset changed transport"
                    else:
                        replacements = [row for row in events if row["action"] == "replace"]
                        if bool(replacements) != expected["replace_requested"]:
                            reason = f"replacement request {replacements} != {expected['replace_requested']}"
                        elif replacements:
                            value = replacements[-1]
                            if value["file"] != expected["replace_first"].lower() or (value["loop_file"] or "") != expected["replace_second"].lower():
                                reason = "replacement resolved file names differ"
                            elif value["generation"] != 12 or value["loop"] != bool(expected["replace_flags"]):
                                reason = "replacement changed handle generation or loop flag"
                        if any(row["action"] in ("play", "stop") for row in events):
                            reason = "replace performed stop/play rather than in-place request"
                except Exception as error:
                    reason = str(error)
                if reason:
                    failures[name] += 1
                    if failures[name] <= 12:
                        report["failures"].append({"sample": sample["sample"], "name": name,
                                                   "arguments": arguments, "fixture": fixture,
                                                   "discard": discard, "reason": reason})
        calls = [{"name": name, "hash": oracle.entries[name]["hash"], "cases": counts[name],
                  "failures": failures[name], "status": "passed" if not failures[name] else "failed",
                  "argument_counts": sorted(argument_counts[name]), "discard_modes": [False, True]}
                 for name in NAMES]
        report["samples"].append({"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
                                  "calls": calls, "helpers": oracle.recovered,
                                  "stream_head": oracle.stream_head, "handle_slot": oracle.music_slot})
        print(f"{sample['sample']}: {sum(counts.values())} audio completion comparisons; "
              f"{sum(failures.values())} mismatches", flush=True)
    report["comparisons"] = sum(call["cases"] for sample in report["samples"] for call in sample["calls"])
    report["lifecycle_checks"]=lifecycle_checks()
    report["mismatches"] = sum(call["failures"] for sample in report["samples"] for call in sample["calls"])
    report["source_changed_during_test"] = before != source_identity()
    report["test_sources"] = {name: hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
                              for name in ("test_audio_completion.py", "original_audio_completion.py",
                                           "original_audio_remaining.py", "original_extsound.py",
                                           "original_audio.py", "original_opcode.py")}
    report["status"] = "passed" if not report["mismatches"] and not report["source_changed_during_test"] else "failed"
    folder = archive_dir("audio_completion_tests")
    write_json(folder/"20261110_audio_completion_tests.json", report)
    write_json(ROOT/"reports/audio_completion_tests.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
