"""Original numbered SE routing, controls and cached voice-name comparisons."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from audit_player_semantics import registered_providers, source_identity
from original_audio_remaining import NumberedAudioOracle

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"convert"))
from port_common import ROOT, archive_dir, timestamp, write_json


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="append")
    args = parser.parse_args()
    samples = json.loads((ROOT/"reports/opcode_dispatch.json").read_text(encoding="utf-8"))["samples"]
    dispatch = json.loads((ROOT/"reports/syscall_dispatch.json").read_text(encoding="utf-8"))["samples"]
    by_name = {row["sample"]: row for row in dispatch}
    if args.sample:
        samples = [row for row in samples if row["sample"] in args.sample]
    before = source_identity()
    report = {"schema": 1, "timestamp": timestamp(), "source_identity": before,
              "samples": [], "failures": [], "complete_engine_semantics": False,
              "scope": "20 numbered SE channels, cached sound/voice request names, shared one-shot gain/pan, "
                       "WAV/OGG routing, absent/stopped streams, playback suppression, control-master selection, "
                       "signed-overflow gain arithmetic and return disposal",
              "host_boundaries": ["single-thread critical sections", "explicit device/fast-mode input",
                                  "fixture VFS membership", "host MSVCRT CP932 path splitting",
                                  "final audio decoder creation", "diagnostic dialog display",
                                  "fixed multimedia clock"],
              "limits": ["physical decoding, worker completion/fade scheduling and audible output are separate",
                         "sound_ch_wait and music replacement/stat-reset are not implemented by this suite",
                         "invalid source signatures retain strict runtime diagnostics",
                         "sound_ch_pan is absent in BOKUTSUBA and SWITCH"]}
    files = {"fixture.wav", "fixture.ogg", "only.ogg", "\u97f3.ogg"}
    for sample in samples:
        oracle = NumberedAudioOracle(sample, by_name[sample["sample"]]["entries"])
        counts, failures, argc = Counter(), Counter(), {}
        # Every real array slot, including first/last and multibyte cached names.
        cases = []
        for index in range(20):
            for name in ("$sound_ch_get_fn$", "$voice_ch_get_fn$"):
                for request in ("", "old/path", "\u97f3"):
                    cases.append((name, [index], {"request": request}))
        for index in (0, 7, 19):
            for present in (False, True):
                for disabled in (False, True):
                    basic = {"previous": present, "disabled": disabled, "gain": 413,
                             "pan": 2345 if oracle.sound_pan else 0}
                    cases.extend([("$sound_ch_stop", [index], basic),
                                  ("$sound_ch_stat", [index], basic),
                                  ("$sound_ch_stat", [index], {**basic, "active": False})])
                    for value in (-2147483648, -1000, 0, 413, 1000, 2147483647):
                        cases.append(("$sound_ch_vol", [index, value],
                                      {**basic, "control_master": 733, "master": 281}))
                    if oracle.sound_pan:
                        for value in (-20000, -10000, -4321, 0, 4321, 10000, 20000):
                            cases.append(("$sound_ch_pan", [index, value], basic))
                    for duration in (-31, 0, 1, 150):
                        cases.append(("$sound_ch_fade", [index, 413, duration],
                                      {**basic, "control_master": 733, "master": 281}))
                        cases.append(("$sound_ch_fadeout", [index, duration], basic))
                        cases.append(("$sound_ch_fadeout", [index, duration], {**basic, "fast": True}))
            requests = [("fixture", None), ("fixture", 0), ("fixture", 1), ("fixture", -2),
                        ("sub/Fixture", 1), ("only", 0), ("\u97f3", 0),
                        ("missing", 1), ("", 1)]
            for request, loop in requests:
                for disabled, ultra, master in ((False, False, 733), (False, False, 0),
                                                (True, False, 733), (False, True, 733)):
                    cases.append(("$sound_ch", [index, request]+([] if loop is None else [loop]),
                                  {"gain": 413, "pan": 2345 if oracle.sound_pan else 0,
                                   "disabled": disabled, "ultra": ultra, "master": master}))
        for name, arguments, fixture in cases:
            for discard in (False, True):
                counts[name] += 1
                argc.setdefault(name, set()).add(len(arguments))
                events, reference = [], {}

                def asset(filename):
                    return reference["lua"].table_from({"file": filename.lower(), "duration_ms": 100})

                def audio(action, spec):
                    events.append({"action": action, "id": spec["id"], "file": spec["file"],
                                   "gain": spec["volume"], "pan": spec["pan"],
                                   "fade": spec["fade_ms"], "loop": bool(spec["loop"])})
                    if action == "playing":
                        return fixture.get("active", True)
                    return True

                lua, _, _, system = registered_providers(overrides={
                    "now": lambda: 2000, "wall_now": lambda: 2000,
                    "audio": audio, "media_asset": asset,
                    "audio_disabled": fixture.get("disabled", False),
                    "fast_mode": lambda vm: fixture.get("fast", False),
                    "resource_names": {value: True for value in files}})
                reference["lua"] = lua
                system["providers"]["$is_fast_mode"] = lambda current, values: int(fixture.get("fast", False))
                media, settings = system["state"]["media"], system["state"]["settings"]
                settings["soundvolume"] = fixture.get("master", 1000)
                settings["voicevolume"] = fixture.get("control_master", 1000)
                settings["ultra_fast_mode"] = fixture.get("ultra", False)
                index = arguments[0]
                channel = "sound_ch_"+str(index)
                media["sound_ch_volume"] = fixture.get("gain", 1000)
                media["sound_ch_pan"] = fixture.get("pan", 0)
                request = fixture.get("request", "previous")
                media[channel+"_requested"] = request
                media["voice_ch_"+str(index)+"_requested"] = request
                if fixture.get("previous", True):
                    media["channels"][channel] = lua.table_from({
                        "id": channel, "name": "previous", "file": "previous.wav",
                        "volume": 777, "channel_volume": 777, "pan": 0,
                        "duration_ms": 100, "generation": 0, "started": 1900})
                vm = lua.table()
                vm["frame"] = lambda current: lua.table_from({"script": {"name": "numbered-audio-fixture"}},
                                                            recursive=True)
                reason = None
                try:
                    oracle.files = files
                    oracle.prepare_numbered(index, **fixture)
                    expected = oracle.call_numbered(name, arguments, discard)
                    actual = system["providers"][name](vm, lua.table_from(arguments))
                    if not discard and expected["result"] != [actual]:
                        reason = f"return {actual!r} != {expected['result']!r}"
                    elif discard and expected["result"]:
                        reason = "discard mode retained an original return value"
                    if media["sound_ch_volume"] != expected["gain"]:
                        reason = f"shared next gain {media['sound_ch_volume']} != {expected['gain']}"
                    if oracle.sound_pan and media["sound_ch_pan"] != expected["pan"]:
                        reason = f"shared next pan {media['sound_ch_pan']} != {expected['pan']}"
                    if media[channel+"_requested"] != expected["request"]:
                        reason = "cached request name changed differently"
                    controls = {value["action"]: value for value in events}
                    if name == "$sound_ch_vol" and fixture.get("previous", True) and not fixture.get("disabled"):
                        if controls.get("volume", {}).get("gain") != expected["stream_gain"]:
                            reason = f"volume control {controls} != {expected['stream_gain']}"
                    if name == "$sound_ch_pan" and fixture.get("previous", True):
                        if controls.get("pan", {}).get("pan") != expected["stream_pan"]:
                            reason = "raw pan request differs"
                    if name == "$sound_ch_fade" and fixture.get("previous", True):
                        value = controls.get("volume", {})
                        if value.get("gain") != expected["fade_gain"] or value.get("fade") != expected["fade_duration"]:
                            reason = f"fade request {value} != original gain/duration {expected['fade_gain']}/{expected['fade_duration']}"
                    if name == "$sound_ch_fadeout" and fixture.get("previous", True):
                        wanted = 0 if fixture.get("fast") else max(0, arguments[1])
                        if (controls.get("fadeout", {}).get("fade") != wanted
                                or expected["fade_duration"] != max(1, wanted)):
                            reason = "fadeout duration or fast suppression differs"
                    if name in ("$sound_ch_stop", "$sound_ch_fadeout"):
                        if media["channels"][channel] is not None or expected["handle"] != 0:
                            reason = "stopped channel remains assigned"
                    if name == "$sound_ch":
                        plays = [value for value in events if value["action"] == "play"]
                        if len(plays) != len(expected["plays"]):
                            reason = "playback suppression differs"
                        if plays:
                            a, b = plays[0], expected["plays"][0]
                            if (a["file"].lower() != b["filename"].lower()
                                    or a["gain"] != b["gain"] or a["loop"] != (b["loop"] != 0)):
                                reason = f"decoder request {a} != {b}"
                        stops = [value for value in events if value["action"] == "stop"]
                        if bool(stops) != (fixture.get("previous", True) and not expected["active"]):
                            reason = "previous stream stop differs"
                except Exception as error:
                    reason = str(error)
                if reason:
                    failures[name] += 1
                    if failures[name] <= 16:
                        report["failures"].append({"sample": sample["sample"], "name": name,
                                                   "arguments": arguments, "fixture": fixture,
                                                   "discard": discard, "reason": reason})
        calls = [{"hash": oracle.entries[name]["hash"], "name": name, "cases": counts[name],
                  "failures": failures[name], "status": "passed" if not failures[name] else "failed",
                  "argument_counts": sorted(argc[name]), "discard_modes": [False, True]}
                 for name in sorted(counts)]
        report["samples"].append({"sample": sample["sample"], "exe_sha256": sample["exe_sha256"],
                                  "calls": calls, "handle_array": oracle.sound_handles,
                                  "request_array": oracle.sound_names, "shared_gain": oracle.sound_gain,
                                  "shared_pan": oracle.sound_pan,
                                  "control_master": oracle.control_master_slot,
                                  "decoder_boundary": oracle.numbered_decoder})
        print(f"{sample['sample']}: {sum(counts.values())} numbered audio comparisons; "
              f"{sum(failures.values())} mismatches", flush=True)
    report["comparisons"] = sum(row["cases"] for sample in report["samples"] for row in sample["calls"])
    report["mismatches"] = sum(row["failures"] for sample in report["samples"] for row in sample["calls"])
    report["source_changed_during_test"] = source_identity() != before
    report["test_sources"] = {
        name: hashlib.sha256((ROOT/"scripts/analysis"/name).read_bytes()).hexdigest()
        for name in ("original_opcode.py", "original_strings.py", "original_calls.py",
                     "native_function.py", "original_audio.py", "original_extsound.py",
                     "original_audio_remaining.py", "test_audio_remaining.py")}
    report["status"] = "passed" if not report["mismatches"] and not report["source_changed_during_test"] else "failed"
    folder = archive_dir("audio_remaining")
    write_json(folder/(folder.name[:8]+"_audio_remaining.json"), report)
    write_json(ROOT/"reports/audio_remaining_tests.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(run())
